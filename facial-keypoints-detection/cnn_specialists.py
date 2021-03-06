import os
from collections import OrderedDict
import numpy as np
from pandas.io.parsers import read_csv 
from datetime import datetime
from lasagne import layers
from lasagne.updates import nesterov_momentum
from nolearn.lasagne import NeuralNet
from nolearn.lasagne import BatchIterator
from sklearn.utils import shuffle
from sklearn.base import clone
from sklearn.metrics import mean_squared_error
import theano
from pandas import DataFrame
import cPickle as pickle
import sys

sys.setrecursionlimit(10000)
np.random.seed(42)

FTRAIN = 'training.csv'
FTEST = 'test.csv'

def load(test = False, cols = None):
    fname = FTEST if test else FTRAIN
    df = read_csv(os.path.expanduser(fname)) 

    df['Image'] = df['Image'].apply(lambda im: np.fromstring(im, sep = ' '))

    if cols:
        df = df[list(cols) + ['Image']]
    
    print df.count()
    df = df.dropna()
    X = np.vstack(df['Image'].values) / 255
    X = X.astype(np.float32)

    if not test:
        y = df[df.columns[:-1]].values
        y = (y - 48) / 48 
        X, y = shuffle(X, y, random_state = 42)
        y = y.astype(np.float32)

    else:
        y = None

    return X, y

def load_2d(test = False, cols = None):
    X, y = load(test = test, cols = cols)
    X = X.reshape(-1, 1, 96, 96)
    return X, y

def float32(k):
    return np.cast['float32'](k)

class FlipBatchIterator(BatchIterator):

    flip_indices = [
            (0, 2), (1, 3), (4, 8), (5, 9), (6, 10), (7, 11), (12, 16),
            (13, 17), (14, 18), (15, 19), (22, 24), (23, 25),
            ]

    def transform(self, Xb, yb):
        Xb, yb = super(FlipBatchIterator, self).transform(Xb, yb)

        bs = Xb.shape[0]
        indices = np.random.choice(bs, bs / 2, replace = False)
        Xb[indices] = Xb[indices, :, :, ::-1]

        if yb is not None:
            yb[indices, ::2] = yb[indices, ::2] * -1
            for a, b in self.flip_indices:
                yb[indices, a], yb[indices, b] = (yb[indices, b], yb[indices, a])

        return Xb, yb

class AdjustVariable(object):
    def __init__(self, name, start = 0.03, stop = 0.001):
        self.name = name
        self.start, self.stop = start, stop
        self.ls = None

    def __call__(self, nn, train_history):
        if self.ls is None:
            self.ls = np.linspace(self.start, self.stop, nn.max_epochs)

        epoch = train_history[-1]['epoch']
        new_value = float32(self.ls[epoch - 1])
        getattr(nn, self.name).set_value(new_value)

class EarlyStopping(object):
    def __init__(self, patience = 100):
        self.patience = patience
        self.best_valid = np.inf
        self.best_valid_epoch = 0
        self.best_weights = None

    def __call__(self, nn, train_history):
        current_valid = train_history[-1]['valid_loss']
        current_epoch = train_history[-1]['epoch']
        if current_valid < self.best_valid:
            self.best_valid = current_valid
            self.best_valid_epoch = current_epoch
            self.best_weights = [w.get_value() for w in nn.get_all_params()]
        elif self.best_valid_epoch + self.patience < current_epoch:
            print "Early Stoppoing"
            print "Best valid loss vas {:.6f} at epoch {}.".format(
                    self.best_valid, self.best_valid_epoch)
            nn.load_weights_from(self.best_weights)
            raise StopIteration()

SPECIALIST_SETTINGS = [
        dict(columns = ('left_eye_center_x', 'left_eye_center_y', 
            'right_eye_center_x', 'right_eye_center_y'),
            flip_indices = ((0, 2), (1, 3))),

        dict(columns = ('nose_tip_x', 'nose_tip_y'),
            flip_indices = ()),

        dict(columns = ('mouth_left_corner_x', 'mouth_left_corner_y',
            'mouth_right_corner_x', 'mouth_right_corner_y',
            'mouth_center_top_lip_x', 'mouth_center_top_lip_y'),
            flip_indices = ((0, 2), (1, 3))),

        dict(columns=('mouth_center_bottom_lip_x', 'mouth_center_bottom_lip_y'),
            flip_indices = ()),

        dict(columns = ('left_eye_inner_corner_x', 'left_eye_inner_corner_y',
            'right_eye_inner_corner_x', 'right_eye_inner_corner_y',
            'left_eye_outer_corner_x', 'left_eye_outer_corner_y',
            'right_eye_outer_corner_x', 'right_eye_outer_corner_y'),
            flip_indices = ((0, 2), (1, 3), (4, 6), (5, 7))),

        dict(columns = ('left_eyebrow_inner_end_x', 'left_eyebrow_inner_end_y',
            'right_eyebrow_inner_end_x', 'right_eyebrow_inner_end_y',
            'left_eyebrow_outer_end_x', 'left_eyebrow_outer_end_y',
            'right_eyebrow_outer_end_x', 'right_eyebrow_outer_end_y'),
            flip_indices = ((0, 2), (1, 3), (4, 6), (5, 7))),
        ]

net = NeuralNet(
        layers = [
            ('input', layers.InputLayer),

            ('conv1', layers.Conv2DLayer),
            ('pool1', layers.MaxPool2DLayer),
            ('dropout1', layers.DropoutLayer),

            ('conv2', layers.Conv2DLayer),
            ('pool2', layers.MaxPool2DLayer), 
            ('dropout2', layers.DropoutLayer),

            ('conv3', layers.Conv2DLayer),
            ('pool3', layers.MaxPool2DLayer), 
            ('dropout3', layers.DropoutLayer),

            ('hidden4', layers.DenseLayer),
            ('dropout4', layers.DropoutLayer),

            ('hidden5', layers.DenseLayer),
            ('output', layers.DenseLayer),
            ],
        input_shape = (None, 1, 96, 96),
        conv1_num_filters = 32, conv1_filter_size = (3, 3), pool1_ds = (2, 2), dropout1_p = 0.1,
        conv2_num_filters = 32, conv2_filter_size = (3, 3), pool2_ds = (2, 2), dropout2_p = 0.2,
        conv3_num_filters = 32, conv3_filter_size = (3, 3), pool3_ds = (2, 2), dropout3_p = 0.3,
        hidden4_num_units = 1000, dropout4_p = 0.5,
        hidden5_num_units = 1000,
        output_num_units = 30, output_nonlinearity = None,

        update_learning_rate = theano.shared(float32(0.03)),
        update_momentum = theano.shared(float32(0.9)),

        regression = True,
        batch_iterator_train = FlipBatchIterator(batch_size = 128),

        on_epoch_finished = [
            AdjustVariable('update_learning_rate', start = 0.03, stop = 0.0001),
            AdjustVariable('update_momentum', start = 0.9, stop = 0.999),
            EarlyStopping(patience = 200),
            ],
        max_epochs = 10000,
        verbose = 1,
        )


def fit_specialists():
    specialists = OrderedDict()

    for setting in SPECIALIST_SETTINGS:
        cols = setting['columns']
        X, y = load_2d(cols = cols)
        model = clone(net)
        model.output_num_units = y.shape[1]
        model.batch_iterator_train.flip_indices = setting['flip_indices']
        model.max_epochs = int(2e7 / y.shape[0])
        
        print 'Training model for columns {} for {} epochs'.format(
                cols, model.max_epochs)

        model.fit(X, y)
        specialists[cols] = model
    
    with open('net-specialists.pickle', 'wb') as f:
        pickle.dump(specialists, f, -1)

def predict_specialists(frame_specialists = 'net-specialists.pickle'):
    with open(frame_specialists, 'rb') as f:
        specialists = pickle.load(f)

    X = load_2d(test = True)[0]
    y_pred = np.empty((X.shape[0], 0))
    for model in specialists.values():
        y_pred1 = model.predict(X)
        print 'y pred {}'.format(y_pred1.shape)
        y_pred = np.hstack([y_pred, y_pred1])
    y_pred2 = (y_pred + 1) * 48.0
    y_pred2 = y_pred2.clip(0, 96)
    columns = ()
    for cols in specialists.keys():
        columns += cols
    df = DataFrame(y_pred2, columns = columns)
    
    lookup_table = read_csv(os.path.expanduser('IdLookupTable.csv'))

    values = []
    for index, row in lookup_table.iterrows():
        values.append((row['RowId'], df.ix[row.ImageId - 1][row.FeatureName]))
    now_str = datetime.now().isoformat().replace(':', '-')
    filename = 'submission-{}.csv'.format(now_str)
    submission = DataFrame(values, columns = ('RowId', 'Location'))
    submission.to_csv(filename, index = False)
    print 'write {}'.format(filename)

if __name__ == '__main__':
    fit_specialists()
    predict_specialists()


    '''names = [
            'left_eye_center_x', 'left_eye_center_y', 
            'right_eye_center_x', 'right_eye_center_y',
            'nose_tip_x', 'nose_tip_y',
            'mouth_left_corner_x', 'mouth_left_corner_y',
            'mouth_right_corner_x', 'mouth_right_corner_y',
            'mouth_center_top_lip_x', 'mouth_center_top_lip_y',
            'mouth_center_bottom_lip_x', 'mouth_center_bottom_lip_y',
            'left_eye_inner_corner_x', 'left_eye_inner_corner_y',
            'right_eye_inner_corner_x', 'right_eye_inner_corner_y',
            'left_eye_outer_corner_x', 'left_eye_outer_corner_y',
            'right_eye_outer_corner_x', 'right_eye_outer_corner_y',
            'left_eyebrow_inner_end_x', 'left_eyebrow_inner_end_y',
            'right_eyebrow_inner_end_x', 'right_eyebrow_inner_end_y',
            'left_eyebrow_outer_end_x', 'left_eyebrow_outer_end_y',
            'right_eyebrow_outer_end_x', 'right_eyebrow_outer_end_y',
      ]  '''
