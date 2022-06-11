from abc import ABCMeta, abstractmethod
from nox.utils.classes import Nox
import numpy as np
import random
import torch

TRANS_SEP = "@"
ATTR_SEP = "#"


class Abstract_augmentation(Nox):
    """
    Abstract class for augmentations.
    Default - non cachable

    Attributes:
        _is_cachable: whether the output of augmentation is cachable (deterministic) or not (random)
        _caching_keys: keys used to make cache path
    """

    __metaclass__ = ABCMeta

    def __init__(self):
        self._is_cachable = False
        self._caching_keys = ""

    @abstractmethod
    def __call__(self, img, sample=None):
        pass

    def set_seed(self, seed):
        random.seed(seed)
        np.random.seed(seed)
        torch.random.manual_seed(seed)

    def cachable(self):
        return self._is_cachable

    def set_cachable(self, *keys):
        """
        Sets the transformer as cachable
        and sets the _caching_keys according to the input variables.
        """
        self._is_cachable = True
        name_str = "{}{}".format(TRANS_SEP, self.name)
        keys_str = "".join(ATTR_SEP + str(k) for k in keys)
        self._caching_keys = "{}{}".format(name_str, keys_str)
        return

    def caching_keys(self):
        return self._caching_keys
