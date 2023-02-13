"""ipythonでコード片を軽く動作確認したりするため用の準備コード。"""
import functools
import hashlib
import json
import math
import multiprocessing as mp
import os
import pathlib
import pickle
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import polars as pl
import scipy
import sympy
import sklearn
import sklearn.metrics
import sklearn.utils
import joblib

try:
    import tensorflow as tf
except ImportError:
    print("skip: import tensorflow as tf")

try:
    from bashplotlib.scatterplot import plot_scatter
    from bashplotlib.histogram import plot_hist
except ImportError:
    print("ImportError: bashplotlib")

pytoolkit_home = os.environ.get("PYTOOLKIT_HOME")
if pytoolkit_home is not None and pathlib.Path(pytoolkit_home).exists():
    sys.path.append(pytoolkit_home)
    print(f"PYTOOLKIT_HOME: {pytoolkit_home}")
try:
    import pytoolkit as tk
except ImportError:
    print("skip: import pytoolkit as tk")
del pytoolkit_home


def softmax(x):
    # np.exp(x) / np.sum(np.exp(x))
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum()


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def logit(x):
    if x == 1.0:
        return np.inf
    if x == 0.0:
        return -np.inf
    assert 0.0 < x < 1.0, f"Invalid value: {x}"
    return np.log(x / (1.0 - x))


print(f"work_dir: {os.getcwd()}")

df = pl.DataFrame(
    {
        "a": [1, 2, 3, None],
        "b": [4, 5, 6, None],
        "c": [7, 8, np.nan, None],
        "s": [None, "a", "b", "c"],
    }
)

a1 = np.array([0.75, 0.1, 0.15])
b1 = np.array([2, 3, 4])

a2 = np.array([[0.75, 0.1, 0.15], [2, 3, 4]])
b2 = np.array([[3, 4, 5], [4, 5, 6]])

a3 = np.array([[[0.75, 0.1, 0.15], [2, 3, 4]], [[3, 4, 5], [4, 5, 6]]])
b3 = np.array([[[2, 3, 4], [4, 5, 6]], [[3, 4, 5], [1, 2, 3]]])

d = {
    "a": 1,
    "b": 2,
    "c": 3,
    np.nan: "4",
}

get_ipython().run_line_magic("load_ext", "autoreload")
