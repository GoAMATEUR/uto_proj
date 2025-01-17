import logging
import os
import pdb

import torch
from collections import OrderedDict
from utils.model_serialization import load_state_dict
from utils.imports import import_file
from utils.model_zoo import cache_url

class Checkpointer():
    def __init__(
            self,
            model,
            optimizer=None,
            scheduler=None,
            save_dir="",
            save_to_disk=None,
            logger=None,
    ):
        self.model = model
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.save_dir = save_dir
        self.save_to_disk = save_to_disk
        if logger is None:
            logger = logging.getLogger('monodetect.checkpointer')
        self.logger = logger
        
    def save_original_keys(self, name, **kwargs):
        data = {}
        save_dict = OrderedDict()
        for key, value in self.model.state_dict().items():
            # print(key, end="++++")
            if key.startswith("heads"):
                key = key.replace("heads.", "heads.predictor.")
            key = "module.{}".format(key)
            save_dict[key] = value
                
        # data["model"] = self.model.state_dict()
        data["model"] = save_dict
        if self.optimizer is not None:
            data["optimizer"] = self.optimizer.state_dict()
        
        if self.scheduler is not None and hasattr(self.scheduler, "state_dict"):
            data["scheduler"] = self.scheduler.state_dict()
        
        data.update(kwargs)

        save_file = os.path.join(self.save_dir, "{}.pth".format(name))
        # if os.path.exists(save_file):
        self.logger.info("Saving checkpoint to {}".format(save_file))
        torch.save(data, save_file)
        
        self.tag_last_checkpoint(save_file)
        
    def save(self, name, **kwargs):
        data = {}
        data["model"] = self.model.state_dict()
        
        if self.optimizer is not None:
            data["optimizer"] = self.optimizer.state_dict()
        
        if self.scheduler is not None and hasattr(self.scheduler, "state_dict"):
            data["scheduler"] = self.scheduler.state_dict()
        
        data.update(kwargs)

        save_file = os.path.join(self.save_dir, "{}.pth".format(name))
        # if os.path.exists(save_file):
        self.logger.info("Saving checkpoint to {}".format(save_file))
        torch.save(data, save_file)
        
        self.tag_last_checkpoint(save_file)

    def load(self, f=None, use_latest=True):
        if self.has_checkpoint() and use_latest:
            # override argument with existing checkpoint
            f = self.get_checkpoint_file()
        if not f:
            # no checkpoint could be found
            self.logger.info("No checkpoint found. Initializing model from scratch")
            return {}
        self.logger.info("Loading checkpoint from {}".format(f))
        
        checkpoint = self._load_file(f)
        # print(checkpoint.keys())
        # self._extend_10cls(checkpoint, "./", "test")
        self._load_model(checkpoint)
        
        
        if 'iteration' in checkpoint:
            self.logger.info("loading checkpoint from iterations {}".format(checkpoint['iteration']))

        if getattr(self.cfg['SOLVER'], 'LOAD_OPTIMIZER_SCHEDULER'):
            
            if "optimizer" in checkpoint and self.optimizer:
                self.logger.info("Loading optimizer from {}".format(f))
                self.optimizer.load_state_dict(checkpoint.pop("optimizer"))
            
            if "scheduler" in checkpoint and self.scheduler:
                self.logger.info("Loading scheduler from {}".format(f))
                self.scheduler.load_state_dict(checkpoint.pop("scheduler"))

        return checkpoint

    def has_checkpoint(self):
        save_file = os.path.join(self.save_dir, "last_checkpoint")
        return os.path.exists(save_file)

    def get_checkpoint_file(self):
        save_file = os.path.join(self.save_dir, "last_checkpoint")
        try:
            with open(save_file, "r") as f:
                last_saved = f.read()
                last_saved = last_saved.strip()
        except IOError:
            # if file doesn't exist, maybe because it has just been
            # deleted by a separate process
            last_saved = ""
        return last_saved

    def tag_last_checkpoint(self, last_filename):
        save_file = os.path.join(self.save_dir, "last_checkpoint")
        with open(save_file, "w") as f:
            f.write(last_filename)

    def _load_file(self, f):
        return torch.load(f, map_location=torch.device("cpu"))

    def _load_model(self, checkpoint):
        load_state_dict(self.model, checkpoint.pop("model"))
        
    def _extend_10cls(self, checkpoint, save_dir, name):
        state_dict = checkpoint["model"]
        keys = sorted(state_dict.keys())
        prefix = "module"
        if not all(key.startswith(prefix) for key in keys):
            return state_dict
        
        save_dict = OrderedDict()
        for key, value in state_dict.items():
            # ## 填充10类
            if key == "module.heads.predictor.class_head.3.weight":
                v = torch.zeros((10, 128, 1, 1), dtype=torch.float32)
                v[:9, :,:,:] = value
                value = v
            if key == "module.heads.predictor.class_head.3.bias":
                v = torch.zeros(10, dtype=torch.float32)
                v[:9] = value
                value = v
            # print(key)
            save_dict[key] = value #
        data = {}
        data["model"] = save_dict
        data["optimizer"] = checkpoint["optimizer"]
        data["scheduler"] = checkpoint["scheduler"]
        save_file = os.path.join(save_dir, "{}.pth".format(name))
        # if os.path.exists(save_file):
        torch.save(data, save_file)


class DetectronCheckpointer(Checkpointer):
    def __init__(
            self,
            cfg,
            model,
            optimizer=None,
            scheduler=None,
            save_dir="",
            save_to_disk=None,
            logger=None,
    ):
        super(DetectronCheckpointer, self).__init__(
            model, optimizer, scheduler, save_dir, save_to_disk, logger
        )
        self.cfg = cfg.clone()

    def _load_file(self, f):
        if f.startswith("catalog://"):
            paths_catalog = import_file(
                "config.paths_catalog", self.cfg.PATHS_CATALOG, True
            )
            catalog_f = paths_catalog.ModelCatalog.get(f[len("catalog://"):])
            self.logger.info("{} points to {}".format(f, catalog_f))
            f = catalog_f
        if f.startswith("http"):
            # if the file is a url path, download it and cache it
            cached_f = cache_url(f)
            self.logger.info("url {} cached in {}".format(f, cached_f))
            f = cached_f

        loaded = super(DetectronCheckpointer, self)._load_file(f)

        if "model" not in loaded:
            loaded = dict(model=loaded)
        return loaded
