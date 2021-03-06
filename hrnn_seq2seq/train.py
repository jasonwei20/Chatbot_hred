# -*- coding: utf-8 -*-
"""
Created on Wed Oct 11 14:33:36 2017

@author: chuang
"""
### helper.py, process data, and batch data 
import os 
import data_helper as helper
from random import shuffle
#import numpy as np
import config 
import tensorflow as tf 
#import numpy as np 
import seq2seq
#from tensorflow.python.layers.core import Dense
#%%

## first, load and pad data 
## load all data and vocabulary
vocab_path = os.path.join(config.PROCESSED_PATH,'vocab.p')
train_token_path = os.path.join(config.PROCESSED_PATH,'processed_tokens.p')
vocab_to_int,int_to_vocab = helper.load_vocab(vocab_path)
config.source_vocab_size = len(vocab_to_int)
config.target_vocab_size = len(vocab_to_int)
train_enc_tokens, train_dec_tokens, test_enc_tokens,test_dec_tokens = helper.load_training_data(train_token_path)
bucket_ids = helper.bucket_training_data(train_enc_tokens,config.max_conv_length)
batches =  helper.make_batches_of_bucket_ids(bucket_ids,config.batch_size)

## get a batch of data nd pad them 

##ids = bucket_ids['bucket_2'][:10]
#pad_encoder_batch,pad_decoder_batch,source_lengths,target_lengths,hrnn_lengths=helper.get_batch(train_enc_tokens, train_dec_tokens,vocab_to_int,ids)


#pad_encoder_batch = helper.pad_context_batch(encoder_input,vocab_to_int)
#pad_decoder_batch = helper.pad_answer_batch(decoder_input,vocab_to_int)


#%%
## build the network 

# create inpute place holder
input_data, targets, lr, keep_prob, target_sequence_length, max_target_sequence_length, source_sequence_length,hrnn_sequence_length = seq2seq.model_inputs()

# get input shape 
input_shape = tf.shape(input_data)
batch_size_t = input_shape[0]

## here source sequence length might be a problem 

# get hidden state sequence for hrnn layer
with tf.variable_scope("encoder"):
    enc_output, enc_state,hidden_states = seq2seq.encoding_layer(input_data, config.rnn_size, config.num_layers, keep_prob, 
                       source_sequence_length, config.source_vocab_size, 
                       config.encoding_embedding_size)
    
# run hrnn encoding layer 
with tf.variable_scope("hrnn_encoder"):
    enc_output, enc_state = seq2seq.hierarchical_encoding_layer(hidden_states, config.hrnn_size, config.hrnn_num_layers, keep_prob, 
                       hrnn_sequence_length)
    

#%%
## build decoder 

#max_target_sentence_length = 500 
target_vocab_to_int = vocab_to_int
## we need to process targests as well, just leave it as it is for now
dec_input = seq2seq.process_decoder_input(targets,vocab_to_int)

# max_target_sentence_length ust be a scalar, not a tensor, usually it should be the max lenght of all your training data 
# here we just put a random number in our config file 

#with tf.variable_scope("decoder"):
training_decoder_output, inference_decoder_output = seq2seq.decoding_layer(dec_input, enc_state,
                                                                           target_sequence_length, config.max_target_sentence_length,
                                                                           config.rnn_size,config.decoder_num_layers, target_vocab_to_int, 
                                                                           config.target_vocab_size,batch_size_t, 
                                                                           keep_prob, config.decoding_embedding_size)

#%%
# build cost and optimizer

training_logits = tf.identity(training_decoder_output.rnn_output, name='logits')
inference_logits = tf.identity(inference_decoder_output.sample_id, name='predictions')

masks = tf.sequence_mask(target_sequence_length,max_target_sequence_length,dtype=tf.float32,name='masks')

with tf.name_scope('optimization'):
    # Loss function
    cost = tf.contrib.seq2seq.sequence_loss(
            training_logits,
            targets,
            masks)
    
    # optimizer 
    optimizer = tf.train.AdamOptimizer(lr)
    gradients = optimizer.compute_gradients(cost)
    capped_gradients = [(tf.clip_by_value(grad, -1., 1.), var) for grad, var in gradients if grad is not None]
    train_op = optimizer.apply_gradients(capped_gradients)

#%%

## now test if it wokrs at all, try one step 
    
train_ids = bucket_ids['bucket_1'][:10]
pad_encoder_batch,pad_decoder_batch,source_lengths,target_lengths,hrnn_lengths=helper.get_batch(train_enc_tokens, train_dec_tokens,vocab_to_int,train_ids)

with tf.Session() as sess:
    saver= tf.train.Saver(max_to_keep=5)
    sess.run(tf.global_variables_initializer())
    for e in range(1,config.epochs+1):
        shuffle(batches)
        for idx,ids in enumerate(batches,1):
            
            pad_encoder_batch,pad_decoder_batch,source_lengths,target_lengths,hrnn_lengths=helper.get_batch(train_enc_tokens, train_dec_tokens,vocab_to_int,ids)
            if target_lengths[0]>config.max_target_sentence_length: continue
            _,loss = sess.run(
                    [train_op,cost],
                    {input_data:pad_encoder_batch,
                     targets:pad_decoder_batch,
                     lr: config.learning_rate,
                     target_sequence_length:target_lengths,
                     source_sequence_length:source_lengths,
                     keep_prob:config.keep_probability,
                     hrnn_sequence_length:hrnn_lengths}
                    )
                
            
            if idx % 100 == 0:
                train_ids = bucket_ids['bucket_1'][:10]
                pad_encoder_batch,pad_decoder_batch,source_lengths,target_lengths,hrnn_lengths=helper.get_batch(train_enc_tokens, train_dec_tokens,vocab_to_int,ids)
                _,loss = sess.run(
                    [train_op,cost],
                    {input_data:pad_encoder_batch,
                     targets:pad_decoder_batch,
                     lr: config.learning_rate,
                     target_sequence_length:target_lengths,
                     source_sequence_length:source_lengths,
                     keep_prob:config.keep_probability,
                     hrnn_sequence_length:hrnn_lengths}
                    )

#                batch_train_logits = sess.run(
#                        inference_logits,
#                        {input_data: pad_encoder_batch,
#                         source_sequence_length: source_lengths,
#                         target_sequence_length: target_lengths,
#                         hrnn_sequence_length:hrnn_lengths,
#                         keep_prob: 1.0})
                    
                print('epoch: {}/{}, iteration: {}/{}, loss: {}'.format(e,config.epochs,idx,len(batches),loss))
#                result = [int_to_vocab[l] for s in batch_train_logits for l in s if l != 0]
#                print(result)
            if idx % 1000 == 0 :
                saver.save(sess, os.path.join(config.CPT_PATH,'hrnn_bot'),global_step = e)
