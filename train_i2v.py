# -*- coding: utf-8 -*-

import datetime
import pathlib
import pickle

import numpy as np
import torch as t
from torch.optim import Adagrad
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm

from i2v_model import Item2Vec
from i2v_model import SGNS
from train_utils import save_model, configure_weights, UserBatchIncrementDataset
from evaluation import hr_k, mrr_k


def run_epoch(train_dl, epoch, sgns, optim):
    pbar = tqdm(train_dl)
    pbar.set_description("[Epoch {}]".format(epoch))
    train_losses = []

    for batch_titems, batch_citems in pbar:
        loss = sgns(batch_titems, batch_citems)

        train_losses.append(loss.item())
        optim.zero_grad()
        loss.backward()
        optim.step()
        pbar.set_postfix(train_loss=loss.item())

    train_loss = np.array(train_losses).mean()
    print(f'train_loss: {train_loss}')
    return train_loss, sgns


def train(cnfg):
    idx2item = pickle.load(pathlib.Path(cnfg['data_dir'], 'idx2item.dat').open('rb'))
    item2idx = pickle.load(pathlib.Path(cnfg['data_dir'], 'item2idx.dat').open('rb'))

    weights = configure_weights(cnfg, idx2item)
    vocab_size = len(idx2item)

    model = Item2Vec(padding_idx=item2idx['pad'], vocab_size=vocab_size, embedding_size=cnfg['e_dim'])
    sgns = SGNS(embedding=model, vocab_size=vocab_size, n_negs=cnfg['n_negs'], weights=weights)
    dataset = UserBatchIncrementDataset(pathlib.Path(cnfg['data_dir'], cnfg['train']), item2idx['pad'],
                                        cnfg['window_size'])
    train_loader = DataLoader(dataset, batch_size=cnfg['mini_batch'], shuffle=True)

    if cnfg['cuda']:
        sgns = sgns.cuda()

    optim = Adagrad(sgns.parameters(), lr=cnfg['lr'])

    for epoch in range(1, cnfg['max_epoch'] + 1):
        _train_loss = run_epoch(train_loader, epoch, sgns, optim)

    save_model(cnfg, model, sgns)

    # Evaluate on test set
    log_dir = cnfg['log_dir'] + '/' + str(datetime.datetime.now().timestamp())
    writer = SummaryWriter(log_dir=log_dir)

    eval_set = pickle.load(pathlib.Path(cnfg['data_dir'], cnfg['test']).open('rb'))
    k = cnfg['k']

    writer.add_hparams(hparam_dict=cnfg,
                       metric_dict={f'hit_ratio_{k}': hr_k(sgns, eval_set, k), f'mrr_{k}': mrr_k(sgns, eval_set, k)},
                       run_name='ai2v_user_batch')


def calc_loss_on_set(sgns, valid_users_path, pad_idx, batch_size, window_size):
    dataset = UserBatchIncrementDataset(valid_users_path, pad_idx, window_size)
    valid_dl = DataLoader(dataset, batch_size=batch_size, shuffle=False)

    pbar = tqdm(valid_dl)
    valid_losses = []

    for batch_titems, batch_citems in pbar:
        loss = sgns(batch_titems, batch_citems)
        valid_losses.append(loss.item())

    return np.array(valid_losses).mean()


def train_early_stop(cnfg, valid_users_path, pad_idx):
    idx2item = pickle.load(pathlib.Path(cnfg['data_dir'], 'idx2item.dat').open('rb'))

    weights = configure_weights(cnfg, idx2item)
    vocab_size = len(idx2item)

    model = Item2Vec(padding_idx=pad_idx, vocab_size=vocab_size, embedding_size=cnfg['e_dim'])
    sgns = SGNS(embedding=model, vocab_size=vocab_size, n_negs=cnfg['n_negs'], weights=weights)

    if cnfg['cuda']:
        sgns = sgns.cuda()

    optim = Adagrad(sgns.parameters(), lr=cnfg['lr'])
    log_dir = cnfg['log_dir'] + '/' + str(datetime.datetime.now().timestamp())
    writer = SummaryWriter(log_dir=log_dir)

    best_epoch = cnfg['max_epoch'] + 1
    valid_losses = [np.inf]
    patience_count = 0

    for epoch in range(1, cnfg['max_epoch'] + 1):
        dataset = UserBatchIncrementDataset(pathlib.Path(cnfg['data_dir'], cnfg['train']), pad_idx, cnfg['window_size'])
        train_loader = DataLoader(dataset, batch_size=cnfg['mini_batch'], shuffle=True)

        train_loss, sgns = run_epoch(train_loader, epoch, sgns, optim)
        writer.add_scalar("Loss/train", train_loss, epoch)
        # log specific training example loss

        valid_loss = calc_loss_on_set(sgns, valid_users_path, pad_idx, cnfg['mini_batch'], cnfg['window_size'])
        writer.add_scalar("Loss/validation", valid_loss, epoch)
        print(f'valid loss:{valid_loss}')

        if valid_loss < valid_losses[-1]:
            patience_count = 0
            best_epoch = epoch
            save_model(cnfg, model, sgns)

        else:
            patience_count += 1
            if patience_count == cnfg['patience']:
                print(f"Early stopping")
                break

        valid_losses.append(valid_loss)

    writer.flush()
    writer.close()

    return best_epoch


def train_evaluate(cnfg):
    print(cnfg)
    valid_users_path = pathlib.Path(cnfg['data_dir'], cnfg['valid'])
    item2idx = pickle.load(pathlib.Path(cnfg['data_dir'], 'item2idx.dat').open('rb'))

    best_epoch = train_early_stop(cnfg, valid_users_path, item2idx['pad'])

    best_model = t.load(pathlib.Path(cnfg['save_dir'], cnfg['model'] + '_best.pt'))

    valid_loss = calc_loss_on_set(best_model, valid_users_path, item2idx['pad'], cnfg['mini_batch'], cnfg['window_size'])
    return {'valid_loss': (valid_loss, 0.0), 'early_stop_epoch': (best_epoch, 0.0)}
