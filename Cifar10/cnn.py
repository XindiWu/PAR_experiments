from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import sys
import time
import argparse

import numpy as np
import tensorflow as tf

from util import nn_util
from util import data_util
from util import test_util

class ResNet(object):
    def __init__(self, x, y, args):
        self.x = tf.reshape(x, shape=[-1, 32, 32, 3])
        self.y = y
        self.keep_prob = tf.placeholder(tf.float32)
        self.model_path = os.path.join('cachedir/models/', args.output)
        self.learning_rate = tf.placeholder(tf.float32)

        if int(args.input_epoch) == 0:
            self.load_model_path = os.path.join('cachedir/models/', args.input)
        else:
            self.load_model_path = os.path.join('cachedir/models/', args.input, str(args.input_epoch))

        n = 5
        reuse = False

        with tf.variable_scope('cnn'):

            layers = []
            with tf.variable_scope('conv0', reuse=reuse):
                conv0 = nn_util.conv_bn_relu_layer(self.x, [3, 3, 3, 16], 1)
                nn_util.activation_summary(conv0)
                layers.append(conv0)

            for i in range(n):
                with tf.variable_scope('conv1_%d' % i, reuse=reuse):
                    if i == 0:
                        conv1 = nn_util.residual_block(layers[-1], 16, first_block=True)
                    else:
                        conv1 = nn_util.residual_block(layers[-1], 16)
                    nn_util.activation_summary(conv1)
                    layers.append(conv1)

            for i in range(n):
                with tf.variable_scope('conv2_%d' % i, reuse=reuse):
                    conv2 = nn_util.residual_block(layers[-1], 32)
                    nn_util.activation_summary(conv2)
                    layers.append(conv2)

            for i in range(n):
                with tf.variable_scope('conv3_%d' % i, reuse=reuse):
                    conv3 = nn_util.residual_block(layers[-1], 64)
                    layers.append(conv3)
                assert conv3.get_shape().as_list()[1:] == [8, 8, 64]

            in_channel = layers[-1].get_shape().as_list()[-1]
            bn_layer = nn_util.batch_normalization_layer(layers[-1], in_channel)
            relu_layer = tf.nn.relu(bn_layer)
            # B x 64
            global_pool = tf.reduce_mean(relu_layer, [1, 2])
            assert global_pool.get_shape().as_list()[-1:] == [64]

            
            with tf.variable_scope('fc', reuse=reuse):
                in_channel = layers[-1].get_shape().as_list()[-1]
                bn_layer = nn_util.batch_normalization_layer(layers[-1], in_channel)
                relu_layer = tf.nn.relu(bn_layer)
                global_pool = tf.reduce_mean(relu_layer, [1, 2])

                assert global_pool.get_shape().as_list()[-1:] == [64]
                output = nn_util.output_layer(global_pool, 10)
                layers.append(output)

        y_conv = output

        self.loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(labels=self.y, logits=y_conv))
        self.pred = tf.argmax(y_conv, 1)

        regu_losses = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
        self.loss = tf.add_n([self.loss] + regu_losses)

        self.correct_prediction = tf.equal(tf.argmax(y_conv, 1), tf.argmax(self.y, 1))
        self.accuracy = tf.reduce_mean(tf.cast(self.correct_prediction, tf.float32))

        self.optimizer = tf.train.MomentumOptimizer(self.learning_rate, momentum=0.9).minimize(self.loss)

    def load_initial_weights(self, session):
        for v in tf.trainable_variables():
            saveName = v.name.replace('/', '_')
            # print (saveName)
            if saveName.startswith('cnn'):
                data = np.load(self.load_model_path + '/cnn_' + saveName[4:] + '.npy')
                session.run(v.assign(data))

def generate_train_batch(args, train_data, train_labels, train_batch_size, padding_size, i):
    batch_data = train_data[i*train_batch_size:(i+1)*train_batch_size, :]
    batch_label = train_labels[i*train_batch_size:(i+1)*train_batch_size, :]

    if args.augmentation:
        batch_data = data_util.random_crop_and_flip(batch_data, padding_size=padding_size)

    return batch_data, batch_label

def generate_test_batch(args, test_data, test_labels, test_batch_size, padding_size, i):
    batch_data = test_data[i*test_batch_size:(i+1)*test_batch_size, :]
    batch_label = test_labels[i*test_batch_size:(i+1)*test_batch_size, :]

    return batch_data, batch_label

def train(args, model, Xtrain, Ytrain, Xtest, Ytest):
    model_path = os.path.join('cachedir/models', args.output)
    if not os.path.exists(model_path):
        os.mkdir(model_path)

    with tf.Session() as sess:
        print('Starting training')
        sess.run(tf.global_variables_initializer())
        # model.load_initial_weights(sess)
        num_batches = Xtrain.shape[0] // args.batch_size

        validation = False

        test_num_batches = Xtest.shape[0] // args.batch_size

        best_validate_accuracy = 0
        score = 0

        for epoch in range(args.epochs):
            begin = time.time()

            # shuffle the data
            order = np.random.permutation(Xtrain.shape[0])
            Xtrain = Xtrain[order, :]
            Ytrain = Ytrain[order]

            # train
            train_accuracies = []
            losses = []

            # update the learning rate!
            if epoch == 100 or epoch == 150 or epoch == 200:
                args.learning_rate = 0.1 * args.learning_rate
                print('Learning rate decayed to %.4f'%args.learning_rate)

            for i in range(num_batches):
                batch_x, batch_y,  = generate_train_batch(args, Xtrain, Ytrain, args.batch_size, 2, i)

                _, acc, loss = sess.run([model.optimizer, model.accuracy, model.loss], feed_dict={x: batch_x,
                                                                                            y: batch_y,
                                                                                            model.keep_prob: 0.5,
                                                                                            model.learning_rate: args.learning_rate})

                train_accuracies.append(acc)
                losses.append(loss)

            train_acc_mean = np.mean(train_accuracies)
            train_loss_mean = np.mean(losses)

            print("Epoch %d, time = %ds, train accuracy = %.4f, train_loss_mean=%.4f" % (
                epoch, time.time() - begin, train_acc_mean, train_loss_mean))
            sys.stdout.flush()

            if (epoch+1)%5==0:
                test_accuracies = []
                for i in range(test_num_batches):
                    batch_x, batch_y = generate_test_batch(args, Xtest, Ytest, args.batch_size, 2, i)

                    acc = sess.run(model.accuracy, feed_dict={x: batch_x,
                                                                y: batch_y,
                                                                model.keep_prob: 1.0,
                                                                model.learning_rate: args.learning_rate})

                    test_accuracies.append(acc)

                score = np.mean(test_accuracies)
                print("Epoch %d Prediction Accuracy = %.4f " % (epoch+1, score))
                epoch_model_path = os.path.join(model_path, str(epoch))
                if not os.path.exists(epoch_model_path):
                    os.mkdir(epoch_model_path)
                for v in tf.trainable_variables():
                    vname = v.name.replace('/', '_')
                    np.save(epoch_model_path+'/' + vname, v.eval())

        print("Best Validated Model Prediction Accuracy = %.4f " % (score))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", type=str, default='ResNet', help='Save model filepath')
    parser.add_argument("-ie", "--input_epoch", type=str, default=199, help='Load model after n epochs')
    parser.add_argument("-i", "--input", type=str, default='ResNet', help='Load model filepath')
    parser.add_argument('-e', '--epochs', type=int, default=200, help='How many epochs to run in total?')
    parser.add_argument('-b', '--batch_size', type=int, default=128, help='Batch size during training per GPU')
    parser.add_argument('-g', '--gpu_id', type=str, default='0', help='gpuid used for trianing')
    parser.add_argument('-lr', '--learning_rate', type=float, default=0.1, help='learning rate')
    parser.add_argument('-au', '--augmentation', type=int, default=1, help='data augmentation?')
    args = parser.parse_args()

    print(args)

    os.environ["CUDA_VISIBLE_DEVICES"]=args.gpu_id

    Xtrain,Ytrain, Xtest, Ytest = data_util.loadDataCifar10()
    if args.augmentation:
        pad_width = ((0, 0), (2, 2), (2, 2), (0, 0))
        Xtrain = np.pad(Xtrain, pad_width=pad_width, mode='constant', constant_values=0)

    num_class = 10

    x = tf.placeholder(tf.float32, (None, 32, 32, 3))
    y = tf.placeholder(tf.float32, (None, num_class))
    model = ResNet(x, y, args)

    train(args, model, Xtrain, Ytrain, Xtest, Ytest)
    test_util.test(args, ResNet)