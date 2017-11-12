import functools
import hashlib
import json
import math
import multiprocessing as mp
import os
import pathlib
import pickle
import shutil
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import sklearn
import sklearn.externals.joblib
import sklearn.utils

df = pd.DataFrame()
df['a'] = [1, 2, 3, np.nan]
df['b'] = [4, 5, 6, np.nan]
df['c'] = [7, 8, np.nan, np.nan]

a1 = np.array([.75, .1, .15])
b1 = np.array([2, 3, 4])

a2 = np.array([[.75, .1, .15], [2, 3, 4]])
b2 = np.array([[3, 4, 5], [4, 5, 6]])

a3 = np.array([[[.75, .1, .15], [2, 3, 4]], [[3, 4, 5], [4, 5, 6]]])
b3 = np.array([[[2, 3, 4], [4, 5, 6]], [[3, 4, 5], [1, 2, 3]]])

if 'USERPROFILE' in os.environ:
    work_dir = pathlib.Path(os.environ['USERPROFILE']).joinpath('Desktop')
elif 'HOME' in os.environ:
    work_dir = pathlib.Path(os.environ['HOME'])
else:
    work_dir = None
if work_dir is not None:
    os.chdir(str(work_dir))

def softmax(x):
    # np.exp(x) / np.sum(np.exp(x))
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum()

def sigmoid(x):
    return 1 / (1 + np.exp(-x))

