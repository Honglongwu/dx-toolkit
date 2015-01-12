"""
use userdict; fall back on .compat.environ or os.environ where possible

dxpy.config.project_context = ...

at save time:
read environ
update with getattr(dxpy, X)


"""

from __future__ import (print_function, unicode_literals)

from collections import UserDict

#from .compat import *

class Config(UserDict):
    def __init__(self):
        pass

    def __getattr__(self, attr):
        return environ[attr]

    def __setattr__(self, attr, value):
        environ[attr] = value

    def __get__(self, item):
        return self.__getattr__(item)

    def __set__(self, item, value):
        self.__setattr__(item, value)

    def update():
        pass

    def write(self, **kwargs):
        pass

    def save(self):
        pass

    def clear(self):
        pass
