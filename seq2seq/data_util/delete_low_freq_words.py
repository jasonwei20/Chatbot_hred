﻿#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Dec 30 15:38:39 2017
code largely from Jason 
"""

#Author: Jason Wei
#Date: Dec. 20, 2017
#This code deletes lines containing words of low frequency.

import pickle
import os
#import jieba
#import time
import multiprocessing
from multiprocessing import Pool
from functools import partial


#%%
# methods
def no_bad_words(line,bad_words): # replace name of person with a replacement word, i.e. "_人名_"
    for word in line:
        if word in bad_words:
            return False
    return True

def process(t,bad_words): 
    enc_tokens, dec_tokens = t
    if no_bad_words(enc_tokens,bad_words) and no_bad_words(dec_tokens,bad_words):
        return (enc_tokens,dec_tokens)
    else:
        return None
    
#%%
if __name__ == "__main__":
    
    PROCESSED_PATH = '../data/processed'
    IMF_server = False
    
    pickle_in = open(os.path.join(PROCESSED_PATH,"vocab.p"),"rb")
    vocab_to_int, int_to_vocab, bad_words = pickle.load(pickle_in)
    
    ### temp solution, delete all names ###
    bad_words.append('_人名_')
    #######################################
    
    print("Finish loading bad_words of size:", len(bad_words), "words.")
    
    pickle_file = os.path.join(PROCESSED_PATH,"processed_tokens.p")
    train_enc_tokens, train_dec_tokens, test_enc_tokens, test_dec_tokens = pickle.load(open(pickle_file,"rb"))
    tokens = zip(train_enc_tokens,train_dec_tokens)
    org_training_size = len(train_enc_tokens)
    
    #%%
    if IMF_server:
        num_cores = 20
    else:
        num_cores = multiprocessing.cpu_count()
    print('Runing filtering in {} cores'.format(num_cores))
    p = Pool(num_cores)
    partial_process = partial(process, bad_words=bad_words)
    results = p.map(partial_process, tokens)
    p.close()
    p.join()
    print('Finish filtering')
    #%%
    r = [t for t in results if t is not None]           
    train_enc_tokens = [t[0] for t in r]
    train_dec_tokens = [t[1] for t in r]
    cur_training_size = len(r)
    
    save_file_path = os.path.join(PROCESSED_PATH,'processed_tokens_clean.p')
    pickle.dump((train_enc_tokens,train_dec_tokens,test_enc_tokens,test_dec_tokens),open(save_file_path,'wb'))
    
    print('Finished filter data. Vocabulary size: {}, original training size: {}, current size: {}'.format(len(vocab_to_int),
          org_training_size,
          cur_training_size))




















#%%

## process data
#
#num_cores = multiprocessing.cpu_count()
#print("Begin multiprocessing with", num_cores, "cores.")
#
#print("Processing the following files:", data_files)
#start = time.time()
#
#chunks = [data_files[x:x+num_cores] for x in range(0, len(data_files), num_cores)] #process the files in chunks of size equal to the number of cores on the cpu.
#print("Processing", num_cores, "files at a time.")
#
#p = Pool(num_cores)
#
#for i in range(len(chunks)):
#    print("Processing chunk", i+1, "of", len(chunks))
#    chunk = chunks[i]
#    p.map(process, chunk)
#
#print("Done processing text.")
#print("Processing time: ", time.time() - start)
