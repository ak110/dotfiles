"""ipythonでコード片を軽く動作確認したりするため用の準備コード。"""
import datetime  # noqa
import functools  # noqa
import hashlib  # noqa
import json  # noqa
import math  # noqa
import multiprocessing as mp  # noqa
import os  # noqa
import pathlib  # noqa
import pickle  # noqa
import random  # noqa
import re  # noqa
import shutil  # noqa
import subprocess  # noqa
import sys  # noqa
import time  # noqa
import traceback  # noqa
import xml.etree.ElementTree as ET  # noqa

import numpy as np  # noqa
import pandas as pd  # noqa
import polars as pl  # noqa
import scipy  # noqa
import sympy  # noqa
import sklearn  # noqa
import sklearn.metrics  # noqa
import sklearn.utils  # noqa
import joblib  # noqa

try:
    import tensorflow as tf  # noqa
except ImportError:
    print("skip: import tensorflow as tf")

try:
    from bashplotlib.scatterplot import plot_scatter  # noqa
    from bashplotlib.histogram import plot_hist  # noqa
except ImportError:
    print("ImportError: bashplotlib")

pytoolkit_home = os.environ.get("PYTOOLKIT_HOME")
if pytoolkit_home is not None and pathlib.Path(pytoolkit_home).exists():
    sys.path.append(pytoolkit_home)
    print(f"PYTOOLKIT_HOME: {pytoolkit_home}")
try:
    import pytoolkit as tk  # noqa
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
        "c": [7.0, 8.0, np.nan, None],
        "s": [None, "a", "b", "c"],
        "d": ["2000/01/01", "2000/01/02", "2000/01/03", "2000/01/04"],
        "t": ["00:01:00", "00:02:00", "00:03:00", "00:04:00"],
        "dt": [
            "2000/01/01 01:00:00",
            "2000/02/01 02:00:00",
            "2000/03/01 03:00:00",
            "2000/04/01 04:00:00",
        ],
    }
)
df = df.with_columns(
    pl.col("d").str.strptime(pl.Date, r"%Y/%m/%d"),
    pl.col("t").str.strptime(pl.Time, r"%H:%M:%S"),
    pl.col("dt").str.strptime(pl.Datetime, r"%Y/%m/%d %H:%M:%S"),
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
