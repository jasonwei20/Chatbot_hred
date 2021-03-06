#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Oct  1 20:28:45 2017

@author: chengyu
"""
import tensorflow as tf 
#import numpy as np
from tensorflow.python.layers.core import Dense
import config 

## Build Seq2seq model 

#%%
## some utilities functions first

def get_device_str(device_id, num_gpus):
  """Return a device string for multi-GPU setup."""
  if num_gpus == 0:
    return "/cpu:0"
  device_str_output = "/gpu:%d" % (device_id % num_gpus)
  #device_str_output = "/gpu:%d" % (1)
  return device_str_output

#%%
def gradient_clip(gradients, max_gradient_norm):
  """Clipping gradients of a model."""
  clipped_gradients, gradient_norm = tf.clip_by_global_norm(
      gradients, max_gradient_norm)
  gradient_norm_summary = [tf.summary.scalar("grad_norm", gradient_norm)]
  gradient_norm_summary.append(
      tf.summary.scalar("clipped_gradient", tf.global_norm(clipped_gradients)))

  return clipped_gradients, gradient_norm_summary, gradient_norm

#%% 
## creating or load embedding layer 
def create_embedding(load=config.load_embeding,vocab_size=config.source_vocab_size,
                    embedding_size=config.encoding_embedding_size,
                    dtype=tf.float32,same=False):
    
    """Create embedding matrix for both encoder and decoder."""
    if same:
        with tf.variable_scope('embeddings',dtype=dtype):
            embedding = tf.Variable(tf.random_uniform([vocab_size,embedding_size]))
        
        encoder_embedding = embedding
        decoder_embedding = encoder_embedding
    else:
        with tf.variable_scope('encoder_embeddings',dtype=dtype):
            encoder_embedding=tf.Variable(tf.random_uniform([vocab_size,embedding_size]))
        with tf.variable_scope('decoder_embeddings',dtype=dtype):
            decoder_embedding=tf.Variable(tf.random_uniform([vocab_size,embedding_size]))
            
    return encoder_embedding,decoder_embedding
#%%
# some functions for creating cells
def _single_cell(unit_type,num_units,keep_prob,residual_connection=False, device_str=None):
    """Create an instance of a single RNN cell."""
    
    if unit_type == 'lstm':
        single_cell =  tf.contrib.rnn.LSTMCell(num_units,
                                               initializer=tf.random_uniform_initializer(-0.1, 0.1))
    elif unit_type == "gru":
        single_cell = tf.contrib.rnn.GRUCell(num_units)
    elif unit_type == "layer_norm_lstm":
        single_cell = tf.contrib.rnn.LayerNormBasicLSTMCell(
            num_units,layer_norm=True)
    elif unit_type == "nas":
        single_cell = tf.contrib.rnn.NASCell(num_units)
    else:
        raise ValueError("Unknown unit type %s!" % unit_type)

    ## apply drop out 
    single_cell = tf.contrib.rnn.DropoutWrapper(single_cell,output_keep_prob=keep_prob)
    
    if residual_connection:
        single_cell = tf.contrib.rnn.ResidualWrapper(single_cell)
    
    ## Device Wrapper
    if device_str:
        single_cell = tf.contrib.rnn.DeviceWrapper(single_cell, device_str)
        print("  %s, device=%s" % (type(single_cell).__name__, device_str))
    
    return single_cell
    
def _cell_list(unit_type, num_units, num_layers, num_residual_layers,
               keep_prob,single_cell_fn=None):
    """Create a list of RNN cells."""
    
    cell_list = []
    if not single_cell_fn:
        single_cell_fn = _single_cell
    
    for i in range(num_layers):
        single_cell = single_cell_fn(unit_type,num_units,keep_prob,
                                     residual_connection=(i >= num_layers - num_residual_layers),device_str= get_device_str(i, config.num_gpus))
        cell_list.append(single_cell)
    
    return cell_list 

def _create_rnn_cell(unit_type, num_units, num_layers, num_residual_layers, keep_prob, single_cell_fn=None):
    """
    Args:
    unit_type: string representing the unit type, i.e. "lstm".
    num_units: the depth of each unit.
    num_layers: number of cells.
    num_residual_layers: Number of residual layers from top to bottom. For
      example, if `num_layers=4` and `num_residual_layers=2`, the last 2 RNN
      cells in the returned list will be wrapped with `ResidualWrapper`
    keep_prob:  floating point value between 0.0 and 1.0
    """
    cell_list = _cell_list(unit_type=unit_type,
                         num_units=num_units,
                         num_layers=num_layers,
                         num_residual_layers=num_residual_layers,
                         keep_prob=keep_prob,
                         single_cell_fn=single_cell_fn)
    
    if len(cell_list) == 1:  # Single layer.
        return cell_list[0]
    else:  # Multi layers
        return tf.contrib.rnn.MultiRNNCell(cell_list)
#%%
    
def _compute_loss(targets, logits,target_sequence_length,max_target_sequence_length,high_level=True):
    """Compute optimization loss."""
    ## we can either use a highleve seq2seq.sequence_loss api or 
    ## we can use more low level softmax_cross_entropy_with_logits api 
    
    target_weights = tf.sequence_mask(target_sequence_length, max_target_sequence_length, dtype=logits.dtype)
    
    if high_level:
        loss = tf.contrib.seq2seq.sequence_loss(
            logits,
            targets,
            target_weights)
    else:
        crossent = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=targets, logits=logits)
        loss = tf.reduce_sum(crossent * target_weights) / tf.to_float(config.batch_size)
    
    return loss

def _get_learning_rate_decay(global_step, config):
    """Get learning rate decay."""
    if (config.learning_rate_decay_scheme and config.learning_rate_decay_scheme == "luong"):
          start_decay_step = int(config.num_train_steps / 2)
          decay_steps = int(config.num_train_steps / 10)  # decay 5 times
          decay_factor = 0.5
    else:
          start_decay_step = config.start_decay_step
          decay_steps = config.decay_steps
          decay_factor = config.decay_factor
    print("  decay_scheme=%s, start_decay_step=%d, decay_steps=%d,decay_factor %g" %
          (config.learning_rate_decay_scheme, config.start_decay_step, config.decay_steps, config.decay_factor))
    
    return tf.cond(
            global_step < start_decay_step,
            lambda: config.learning_rate,
            lambda: tf.train.exponential_decay(
                            config.learning_rate,
                            (global_step - start_decay_step),
                            decay_steps, decay_factor, staircase=True), 
                            name="learning_rate_decay_cond")             ## using staircase decay 



#%%
def model_inputs():
    """
    Create TF Placeholders for input, targets, learning rate, and lengths of source and target sequences.
    :return: Tuple (input, targets, learning rate, keep probability, target sequence length,
    max target sequence length, source sequence length)
    """
    
    input_data = tf.placeholder(tf.int32,[None,None],name='input')
    targets = tf.placeholder(tf.int32,[None,None],name='targets')
    lr = tf.placeholder(tf.float32,name='learning_rate')
    keep_pro = tf.placeholder(tf.float32,name='keep_prob')
    target_sequence_length = tf.placeholder(tf.int32,(None,),name='target_sequence_length')
    max_target_sequence_length = tf.reduce_max(target_sequence_length,name='max_target_len')
    source_sequence_length = tf.placeholder(tf.int32,(None,),name='source_sequence_length')
    #hrnn_sequence_length = tf.placeholder(tf.int32,(None,),name = 'hrnn_sequence_length')
    
    return input_data, targets, lr, keep_pro, target_sequence_length, max_target_sequence_length, source_sequence_length,#hrnn_sequence_length

#input_data, targets, lr, keep_pro, target_sequence_length, max_target_sequence_length, source_sequence_length = model_inputs()
#%%
def model_inputs_iter(iterator):
    """
    Create TF Placeholders for input, targets, learning rate, and lengths of source and target sequences.
    :return: Tuple (input, targets, learning rate, keep probability, target sequence length,
    max target sequence length, source sequence length)
    """
    
    input_data = tf.placeholder(tf.int32,[None,None],name='input')
    targets = tf.placeholder(tf.int32,[None,None],name='targets')
    lr = tf.placeholder(tf.float32,name='learning_rate')
    keep_pro = tf.placeholder(tf.float32,name='keep_prob')
    target_sequence_length = tf.placeholder(tf.int32,(None,),name='target_sequence_length')
    max_target_sequence_length = tf.reduce_max(target_sequence_length,name='max_target_len')
    source_sequence_length = tf.placeholder(tf.int32,(None,),name='source_sequence_length')
    #hrnn_sequence_length = tf.placeholder(tf.int32,(None,),name = 'hrnn_sequence_length')
    
    return input_data, targets, lr, keep_pro, target_sequence_length, max_target_sequence_length, source_sequence_length,#hrnn_sequence_length

#%%
def encoding_layer(rnn_inputs, rnn_size, num_layers, keep_prob, 
                   source_sequence_length, source_vocab_size, 
                   encoding_embedding_size,enc_embedding):
    """
    Create encoding layer
    :param rnn_inputs: Inputs for the RNN
    :param rnn_size: RNN Size
    :param num_layers: Number of layers
    :param keep_prob: Dropout keep probability
    :param source_sequence_length: a list of the lengths of each sequence in the batch
    :param source_vocab_size: vocabulary size of source data
    :param encoding_embedding_size: embedding size of source data
    :param enc_embedding: encoder embedding
    :return: tuple (RNN output, RNN state)
    """
    
    ## lookup, turn words into vector
        #enc_embed_input = tf.contrib.layers.embed_sequence(rnn_inputs,source_vocab_size,encoding_embedding_size)

    enc_embed_input=tf.nn.embedding_lookup(enc_embedding,rnn_inputs)
    
    if config.bidirection:
        
            # RNN cell 
        cell_fw = _create_rnn_cell(unit_type=config.cell_type, num_units=rnn_size, 
                                   num_layers=num_layers, 
                                   num_residual_layers=config.num_residual_layers,
                                   keep_prob=keep_prob, 
                                   single_cell_fn=None)
        
        cell_bw = _create_rnn_cell(unit_type=config.cell_type, num_units=rnn_size, 
                                   num_layers=num_layers, 
                                   num_residual_layers=config.num_residual_layers,
                                   keep_prob=keep_prob, 
                                   single_cell_fn=None)
        
        enc_output, bi_encoder_state = tf.nn.bidirectional_dynamic_rnn( 
                                           cell_fw, 
                                           cell_bw,                     
                                           enc_embed_input,
                                           source_sequence_length,
                                           dtype=tf.float32,
                                           swap_memory=True)
    
        enc_output = tf.concat(enc_output,-1)

        bi_encoder_outputs = enc_output
    
    add_more = True
    if add_more:
        uni_cell = _create_rnn_cell(unit_type=config.cell_type, num_units=rnn_size, 
                                   num_layers=config.num_uni_layers, 
                                   num_residual_layers=config.num_residual_layers,
                                   keep_prob=keep_prob, 
                                   single_cell_fn=None)
        


        encoder_outputs, encoder_state = tf.nn.dynamic_rnn(
          uni_cell,
          bi_encoder_outputs,
          dtype=tf.float32,
          sequence_length=source_sequence_length)#,
          #time_major=self.time_major)
        
        #num_uni_layers = 2
        encoder_state = bi_encoder_state+encoder_state

    return encoder_outputs, encoder_state
'''
This code below is not in the gnmt_model
        if num_layers == 1: 
            enc_state = bi_encoder_state
        else:
            encoder_state = []
            for layer_id in range(num_layers):
                encoder_state.append(bi_encoder_state[0][layer_id])  # forward
                encoder_state.append(bi_encoder_state[1][layer_id])  # backward
            
            enc_state = tuple(encoder_state)
            #hidden_states = tf.reshape(enc_output[:,-1,:],[shape[0],shape[1],rnn_size*2])

#        enc_cell = tf.contrib.rnn.MultiRNNCell([make_cell(rnn_size,keep_prob) for _ in range(num_layers)])
        enc_cell = _create_rnn_cell(unit_type=config.cell_type, num_units=rnn_size, 
                                   num_layers=num_layers, 
                                   num_residual_layers=config.num_residual_layers,
                                   keep_prob=keep_prob, 
                                   single_cell_fn=None)
        
        enc_output,enc_state = tf.nn.dynamic_rnn(enc_cell,
                                                 enc_embed_input,
                                                 sequence_length=source_sequence_length,
                                                 dtype=tf.float32,
                                                 swap_memory=True)
        
    return enc_output, enc_state
    '''
#%%
def process_decoder_input(target_data, target_vocab_to_int):
    """
    Preprocess target data for encoding
    :param target_data: Target Placehoder
    :param target_vocab_to_int: Dictionary to go from the target words to an id
    :param batch_size: Batch Size
    :return: Preprocessed target data
    """
    batch_size = tf.shape(target_data)[0]
    ending = tf.strided_slice(target_data,[0,0],[batch_size,-1],[1,1])
    dec_input = tf.concat([tf.fill([batch_size,1],target_vocab_to_int['<GO>']),ending],1)
    
    return dec_input

#%%
def decoding_layer_train(encoder_output,encoder_state, dec_cell, dec_embed_input, 
                         target_sequence_length,source_sequence_length, max_summary_length, 
                         output_layer, keep_prob,batch_size):
    """
    Create a decoding layer for training
    :param encoder_state: Encoder State
    :param dec_cell: Decoder RNN Cell
    :param dec_embed_input: Decoder embedded input
    :param target_sequence_length: The lengths of each sequence in the target batch
    :param max_summary_length: The length of the longest sequence in the batch
    :param output_layer: Function to apply the output layer
    :param keep_prob: Dropout keep probability
    :return: BasicDecoderOutput containing training logits and sample_id
    """
    encoder_output= tf.contrib.seq2seq.tile_batch(encoder_output,multiplier=1)
    source_sequence_length = tf.contrib.seq2seq.tile_batch(source_sequence_length, multiplier=1)
    encoder_state =  tf.contrib.seq2seq.tile_batch(encoder_state, multiplier=1)

    ## attention mechanish
    attn_mech = tf.contrib.seq2seq.LuongAttention(
            config.attention_size,
            encoder_output,
            source_sequence_length,
            name='LuongAttention')
    
    dec_cell = tf.contrib.seq2seq.AttentionWrapper(dec_cell,attn_mech,config.attention_size)
    decoder_initial_state = dec_cell.zero_state(batch_size, tf.float32).clone(cell_state=encoder_state)   # as mentioned in paper massive exploration of nmt, with attention context, decoder initial usually set to zero
    
    ## decode with attention inputs 
    training_helper=tf.contrib.seq2seq.TrainingHelper(inputs=dec_embed_input,
                                                     sequence_length=target_sequence_length,
                                                     time_major=False)
    
    training_decoder = tf.contrib.seq2seq.BasicDecoder(dec_cell,
                                                      training_helper,
                                                      decoder_initial_state,
                                                      output_layer)

    training_decoder_output,_,_=tf.contrib.seq2seq.dynamic_decode(training_decoder,
                                                              impute_finished=True,
                                                              maximum_iterations=max_summary_length)
    
    return training_decoder_output

#%%
def decoding_layer_infer_beam_search(encoder_output,encoder_state, dec_cell, dec_embeddings, start_of_sequence_id,
                         end_of_sequence_id,source_sequence_length,max_target_sequence_length,
                         vocab_size, output_layer, batch_size, keep_prob,beam_width=0,length_penalty_weight=0.7):
    """
    Create a decoding layer for inference
    :param encoder_state: Encoder state
    :param dec_cell: Decoder RNN Cell
    :param dec_embeddings: Decoder embeddings
    :param start_of_sequence_id: GO ID
    :param end_of_sequence_id: EOS Id
    :param max_target_sequence_length: Maximum length of target sequences
    :param vocab_size: Size of decoder/target vocabulary
    :param decoding_scope: TenorFlow Variable Scope for decoding
    :param output_layer: Function to apply the output layer
    :param batch_size: Batch size
    :param keep_prob: Dropout keep probability
    :return: BasicDecoderOutput containing inference logits and sample_id
    """
    
    start_tokens = tf.tile(tf.cast([start_of_sequence_id],dtype=tf.int32),[batch_size],name='start_tokens')
    end_token = end_of_sequence_id
    
    if beam_width> 0:
        encoder_output_beam = tf.contrib.seq2seq.tile_batch(encoder_output,multiplier=beam_width)
        source_sequence_length_beam = tf.contrib.seq2seq.tile_batch(source_sequence_length, multiplier=beam_width)
        encoder_state_beam =  tf.contrib.seq2seq.tile_batch(encoder_state, multiplier=beam_width)
        batch_size_beam = tf.shape(encoder_output_beam)[0]
        
        ## attention mechanish
        attn_mech = tf.contrib.seq2seq.LuongAttention(
                config.attention_size,
                encoder_output_beam,
                source_sequence_length_beam,
                name='LuongAttention')
        
        dec_cell = tf.contrib.seq2seq.AttentionWrapper(dec_cell,attn_mech,config.attention_size)
        decoder_initial_state = dec_cell.zero_state(batch_size_beam, tf.float32).clone(cell_state=encoder_state_beam)   # as mentioned in paper massive exploration of nmt, with attention context, decoder initial usually set to zero
        
        inference_decoder = tf.contrib.seq2seq.BeamSearchDecoder(
                cell = dec_cell,
                embedding=dec_embeddings,
                start_tokens=start_tokens,
                end_token=end_token,
                initial_state = decoder_initial_state,
                beam_width = beam_width,
                output_layer = output_layer,
                length_penalty_weight = length_penalty_weight
                )
        
        inference_decoder_output,_,_ = tf.contrib.seq2seq.dynamic_decode(inference_decoder,
                                                                      impute_finished=False,
                                                                      maximum_iterations=max_target_sequence_length)
    else:
        
        ## attention mechanish
        attn_mech = tf.contrib.seq2seq.LuongAttention(
                config.attention_size,
                encoder_output,
                source_sequence_length,
                name='LuongAttention')
        
        dec_cell = tf.contrib.seq2seq.AttentionWrapper(dec_cell,attn_mech,config.attention_size)
        decoder_initial_state = dec_cell.zero_state(batch_size, tf.float32).clone(cell_state=encoder_state)   # as mentioned in paper massive exploration of nmt, with attention context, decoder initial usually set to zero
        
        inference_helper=tf.contrib.seq2seq.GreedyEmbeddingHelper(dec_embeddings,
                                                                 start_tokens,
                                                                 end_token)
        
        inference_decoder = tf.contrib.seq2seq.BasicDecoder(dec_cell,
                                                           inference_helper,
                                                           decoder_initial_state,
                                                           output_layer)
    
        inference_decoder_output,_,_ = tf.contrib.seq2seq.dynamic_decode(inference_decoder,
                                                                      impute_finished=True,
                                                                      maximum_iterations=max_target_sequence_length)

    return inference_decoder_output
#%%
###############################
## put train and infer together
###############################

def decoding_layer_with_attention(dec_input, encoder_output,encoder_state,
                   source_sequence_length,target_sequence_length, max_target_sequence_length,
                   rnn_size,
                   num_layers, target_vocab_to_int, target_vocab_size,
                   batch_size, keep_prob, decoding_embedding_size,dec_embedding,
                   beam_width=0):
    
    # 1. decoder embeding
    #dec_embeddings = tf.Variable(tf.random_uniform([target_vocab_size,decoding_embedding_size]))
    dec_embed_input = tf.nn.embedding_lookup(dec_embedding,dec_input)
    
    dec_cell = _create_rnn_cell(unit_type=config.cell_type, num_units=rnn_size, 
                                   num_layers=num_layers, 
                                   num_residual_layers=config.num_residual_layers,
                                   keep_prob=keep_prob, 
                                   single_cell_fn=None)

    # 3. output layer to translate the decoder's output at each time 
    output_layer = Dense(target_vocab_size,
                         kernel_initializer=tf.truncated_normal_initializer(mean = 0.0, stddev=0.1))
    
    # 4. Set up a training decoder 
    with tf.variable_scope("decode"):
        training_decoder_output = decoding_layer_train(encoder_output,
                                                       encoder_state,
                                                       dec_cell, 
                                                       dec_embed_input, 
                                                       target_sequence_length, 
                                                       source_sequence_length,
                                                       max_target_sequence_length, 
                                                       output_layer, 
                                                       keep_prob,
                                                       batch_size) 

    with tf.variable_scope("decode", reuse=True):
        start_of_sequence_id = target_vocab_to_int['<GO>']
        end_of_sequence_id = target_vocab_to_int['<EOS>']
        inference_decoder_output = decoding_layer_infer_beam_search(encoder_output,
                                                                    encoder_state,
                                                                    dec_cell, 
                                                                    dec_embedding, 
                                                                    start_of_sequence_id, 
                                                                    end_of_sequence_id, 
                                                                    source_sequence_length,
                                                                    config.max_target_sentence_length, 
                                                                    target_vocab_size, 
                                                                    output_layer, 
                                                                    batch_size, 
                                                                    keep_prob,
                                                                    beam_width)
        
    return training_decoder_output, inference_decoder_output


#%%
