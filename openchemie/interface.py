import torch
from functools import lru_cache
import layoutparser as lp
import pdf2image
from PIL import Image
from huggingface_hub import hf_hub_download
from molscribe import MolScribe
from rxnscribe import RxnScribe, MolDetect
from .textrxnextractor import TextReactionExtractor
from .tableextractor import TableExtractor
from .utils import clean_bbox_output, get_figures_from_pages, convert_to_pil, convert_to_cv2

class OpenChemIE:
    def __init__(self, device=None):
        """
        Initialization function of OpenChemIE
        :param device:
        TODO: every out-facing function should have a description
        """
        if device is None:
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        else:
            self.device = device

    @lru_cache(maxsize=None)
    def init_molscribe(self, ckpt_path=None):
        if ckpt_path is None:
            ckpt_path = hf_hub_download("yujieq/MolScribe", "swin_base_char_aux_1m.pth")
        # TODO: why not just do `self.device = torch.device('cuda')` in __init__
        return MolScribe(ckpt_path, device=torch.device(self.device))
    
    @lru_cache(maxsize=None)
    def init_rxnscribe(self, ckpt_path=None):
        if ckpt_path is None:
            ckpt_path = hf_hub_download("yujieq/RxnScribe", "pix2seq_reaction_full.ckpt")
        return RxnScribe(ckpt_path, device=torch.device(self.device))
    
    @lru_cache(maxsize=None)
    def init_pdfparser(self, ckpt_path=None):
        if ckpt_path is None:
            ckpt_path = "lp://efficientdet/PubLayNet/tf_efficientdet_d1"
        return lp.AutoLayoutModel(ckpt_path, device=self.device)
    
    @lru_cache(maxsize=None)
    def init_moldet(self, ckpt_path=None):
        if ckpt_path is None:
            ckpt_path = hf_hub_download("Ozymandias314/MolDetectCkpt", "best.ckpt")
        return MolDetect(ckpt_path)
        
    @lru_cache(maxsize=None)
    def init_chemrxnextractor(self):
        repo_id = "amberwang/chemrxnextractor-training-modules"
        folder_path = "cre_models_v0.1"
        # TODO: I can live with this, but can you merge these into one file for download? Lower priority
        file_names = ['prod/config.json', 'prod/pytorch_model.bin', 'prod/special_tokens_map.json',
                      'prod/tokenizer_config.json', 'prod/training_args.bin', 'prod/vocab.txt',
                      'role/added_tokens.json', 'role/config.json', 'role/pytorch_model.bin',
                      'role/special_tokens_map.json', 'role/tokenizer_config.json', 'role/training_args.bin',
                      'role/vocab.txt']
        for file_name in file_names:
            file_path = f"{folder_path}/{file_name}"
            hf_hub_download(repo_id, file_path, local_dir='./training_modules')
        # TODO: maybe rename to ChemRxnExtractor
        return TextReactionExtractor("", None)

    @lru_cache(maxsize=None)
    def init_chemner(self):
        # TODO: ChemNER
        pass

    @lru_cache(maxsize=None)
    def init_tableextractor(self):
        return TableExtractor()

    def extract_figures_from_pdf(self, pdf, num_pages=None, output_bbox=False, output_image=True):
        # TODO: I think it makes more sense to split the two functions. Basically the same as the previous function, but every element should have a figure. Remove those without a figure.
        """
        Find and return all figures from a pdf page
        Parameters:
            pdf: path to pdf
            num_pages: process only first `num_pages` pages, if `None` then process all
            output_bbox: whether to output bounding boxes for each individual entry of a table
            output_image: whether to include PIL image for figures. default is True
        Returns:
            list of content in the following format
            [
                { # first figure
                    'title': str,
                    'figure': {
                        'image': PIL image or None,
                        'bbox': list in form [x1, y1, x2, y2],
                    }
                    'table': {
                        'bbox': list in form [x1, y1, x2, y2] or empty list,
                        'content': {
                            'columns': list of column headers,
                            'rows': list of list of row content,
                        } or None
                    }
                    'footnote': str or empty,
                    'page': int
                }
                # more figures
            ]
        """
        pdfparser = self.init_pdfparser()
        pages = pdf2image.convert_from_path(pdf, last_page=num_pages)

        table_ext = self.init_tableextractor()
        table_ext.set_pdf_file(pdf)
        table_ext.set_output_image(output_image)

        table_ext.set_output_bbox(output_bbox)
        
        return table_ext.extract_all_tables_and_figures(pages, pdfparser, content='figures')

    def extract_tables_from_pdf(self, pdf, num_pages=None, output_bbox=False, output_image=True):
        # TODO: Similarly, this one returns all the tables, and the paired figure is optional. If there is a figure paired with a table, provide it.
        """
        Find and return all tables from a pdf page
        Parameters:
            pdf: path to pdf
            num_pages: process only first `num_pages` pages, if `None` then process all
            output_bbox: whether to include bboxes for individual entries of the table
            output_image: whether to include PIL image for figures. default is True
        Returns:
            list of content in the following format
            [
                { # first table
                    'title': str,
                    'figure': {
                        'image': PIL image or None,
                        'bbox': list in form [x1, y1, x2, y2] or empty list,
                    }
                    'table': {
                        'bbox': list in form [x1, y1, x2, y2] or empty list,
                        'content': {
                            'columns': list of column headers,
                            'rows': list of list of row content,
                        }
                    }
                    'footnote': str or empty,
                    'page': int
                }
                # more tables
            ]
        """
        pdfparser = self.init_pdfparser()
        pages = pdf2image.convert_from_path(pdf, last_page=num_pages)

        table_ext = self.init_tableextractor()
        table_ext.set_pdf_file(pdf)
        table_ext.set_output_image(output_image)

        table_ext.set_output_bbox(output_bbox)
        
        return table_ext.extract_all_tables_and_figures(pages, pdfparser, content='tables')

    def extract_molecules_from_pdf(self, pdf, batch_size=16, num_pages=None):
        """
        Get all molecules and their information from a pdf
        Parameters:
            pdf: path to pdf, or byte file
            batch_size: batch size for inference in all models
            num_pages: process only first `num_pages` pages, if `None` then process all
        Returns:
            list of figures and corresponding molecule info in the following format
            [
                {   # first figure
                    'image': ndarray of the figure image,
                    'molecules': [
                        {   # first molecule
                            'bbox': tuple in the form (x1, y1, x2, y2),
                            'score': float,
                            'image': ndarray of cropped molecule image,
                            'smiles': str,
                            'molfile': str
                        },
                        # more molecules
                    ],
                    'page': int
                },
                # more figures
            ]
        """
        figures = self.extract_figures_from_pdf(pdf, num_pages=num_pages, output_bbox=True)
        images = [figure['figure']['image'] for figure in figures]
        results = self.extract_molecules_from_figures(images, batch_size=batch_size)
        for figure, result in zip(figures, results):
            result['page'] = figure['page']
        return results
    
    def extract_molecule_bboxes_from_figures(self, figures, batch_size=16):
        """
        Return bounding boxes of molecules in images
        Parameters:
            figures: list of PIL or ndarray images
            batch_size: batch size for inference
        Returns:
            list of results for each figure in the following format
            [
                [   # first figure
                    {   # first bounding box
                        'category': str,
                        'bbox': tuple in the form (x1, y1, x2, y2),
                        'category_id': int,
                        'score': float
                    },
                    # more bounding boxes
                ],
                # more figures
            ]
        """
        figures = [convert_to_pil(figure) for figure in figures]
        moldet = self.init_moldet()
        return moldet.predict_images(figures, batch_size=batch_size)

    def extract_molecules_from_figures(self, figures, batch_size=16):
        """
        Get all molecules and their information from list of figures
        Parameters:
            figures: list of PIL or ndarray images
            batch_size: batch size for inference
        Returns:
            list of results for each figure in the following format
            [
                {   # first figure
                    'image': ndarray of the figure image,
                    'molecules': [
                        {   # first molecule
                            'bbox': tuple in the form (x1, y1, x2, y2),
                            'score': float,
                            'image': ndarray of cropped molecule image,
                            'smiles': str,
                            'molfile': str
                        },
                        # more molecules
                    ],
                },
                # more figures
            ]
        """
        bboxes = self.extract_molecule_bboxes_from_figures(figures, batch_size=batch_size)
        figures = [convert_to_cv2(figure) for figure in figures]
        results, cropped_images, refs = clean_bbox_output(figures, bboxes)
        molscribe = self.init_molscribe()
        mol_info = molscribe.predict_images(cropped_images, batch_size=batch_size)
        for info, ref in zip(mol_info, refs):
            ref.update(info)
        return results

    def extract_molecule_corefs_from_pdf(self, pdf, batch_size=16, num_pages=None):
        # TODO
        pass
    
    def extract_reactions_from_pdf(self, pdf, batch_size=16, num_pages=None, molscribe=True, ocr=True):
        """
        Get reaction information from figures in pdf
        Parameters:
            pdf: path to pdf, or byte file
            batch_size: batch size for inference in all models
            num_pages: process only first `num_pages` pages, if `None` then process all
            molscribe: whether to predict and return smiles and molfile info
            ocr: whether to predict and return text of conditions
        Returns:
            list of figures and corresponding molecule info in the following format
            [
                {
                    'figure': PIL image
                    'reactions': [
                        {
                            'reactants': [
                                {
                                    'category': str,
                                    'bbox': tuple (x1,x2,y1,y2),
                                    'category_id': int,
                                    'smiles': str,
                                    'molfile': str,
                                },
                                # more reactants
                            ],
                            'conditions': [
                                {
                                    'category': str,
                                    'bbox': tuple (x1,x2,y1,y2),
                                    'category_id': int,
                                    'text': list of str,
                                },
                                # more conditions
                            ],
                            'products': [
                                # same structure as reactants
                            ]
                        },
                        # more reactions
                    ],
                    'page': int
                },
                # more figures
            ]
        """
        figures = self.extract_figures_from_pdf(pdf, num_pages=num_pages, output_bbox=True)
        images = [figure['figure']['image'] for figure in figures]
        results = self.extract_reactions_from_figures(images, batch_size=batch_size, molscribe=molscribe, ocr=ocr)
        for figure, result in zip(figures, results):
            result['page'] = figure['page']
        return results

    def extract_reactions_from_figures(self, figures, batch_size=16, molscribe=True, ocr=True):
        """
        Get reaction information from list of figures
        Parameters:
            figures: list of PIL or ndarray images
            batch_size: batch size for inference in all models
            molscribe: whether to predict and return smiles and molfile info
            ocr: whether to predict and return text of conditions
        Returns:
            list of figures and corresponding molecule info in the following format
            [
                {
                    'figure': PIL image
                    'reactions': [
                        {
                            'reactants': [
                                {
                                    'category': str,
                                    'bbox': tuple (x1,x2,y1,y2),
                                    'category_id': int,
                                    'smiles': str,
                                    'molfile': str,
                                },
                                # more reactants
                            ],
                            'conditions': [
                                {
                                    'category': str,
                                    'bbox': tuple (x1,x2,y1,y2),
                                    'category_id': int,
                                    'text': list of str,
                                },
                                # more conditions
                            ],
                            'products': [
                                # same structure as reactants
                            ]
                        },
                        # more reactions
                    ],
                },
                # more figures
            ]

        """
        pil_figures = [convert_to_pil(figure) for figure in figures]
        rxnscribe = self.init_rxnscribe()
        results = []
        reactions = rxnscribe.predict_images(pil_figures, batch_size=batch_size, molscribe=molscribe, ocr=ocr)
        for figure, rxn in zip(figures, reactions):
            data = {
                'figure': figure,
                'reactions': rxn,
                }
            results.append(data)
        return results

    def extract_named_entities_from_pdf_text(self, pdf, num_pages=None):
        # TODO
        pass

    def extract_reactions_from_pdf_text(self, pdf, num_pages=None):
        """
        Get reaction information from text in pdf
        Parameters:
            pdf: path to pdf
            num_pages: process only first `num_pages` pages, if `None` then process all
        Returns:
            list of pages and corresponding reaction info in the following format
            [
                {
                    'page': page number
                    'reactions': [
                        {
                            'tokens': list of words in relevant sentence,
                            'reactions' : [
                                {
                                    'Reactants': list of tuple,
                                    'Products': list of tuple,
                                    
                                
                                
                                }
                                # more reactions
                            ]
                        }
                        # more reactions in other sentences
                    ]
                },
                # more pages
            ]
        """
        chemrxnextractor = self.init_chemrxnextractor()
        chemrxnextractor.set_pdf_file(pdf)
        chemrxnextractor.set_pages(num_pages)
        return chemrxnextractor.extract_reactions_from_text()


if __name__=="__main__":
    model = OpenChemIE()
