# -*- coding: utf-8 -*-

import numpy as np
import torch as t
import torch.nn as nn

from torch import LongTensor as LT
from torch import FloatTensor as FT


class Bundler(nn.Module):

    def forward(self, data):
        raise NotImplementedError

    def forward_i(self, data):
        raise NotImplementedError

    def forward_o(self, data):
        raise NotImplementedError


class Item2Vec(Bundler):

    def __init__(self, vocab_size=20000, embedding_size=300, padding_idx=0):
        super(Item2Vec, self).__init__()
        self.vocab_size = vocab_size
        self.embedding_size = embedding_size
        self.ivectors = nn.Embedding(self.vocab_size, self.embedding_size, padding_idx=padding_idx)
        self.ovectors = nn.Embedding(self.vocab_size, self.embedding_size, padding_idx=padding_idx)
        self.ivectors.weight = nn.Parameter(t.cat([t.zeros(1, self.embedding_size), FT(self.vocab_size - 1, self.embedding_size).uniform_(-0.5 / self.embedding_size, 0.5 / self.embedding_size)]))
        self.ovectors.weight = nn.Parameter(t.cat([t.zeros(1, self.embedding_size), FT(self.vocab_size - 1, self.embedding_size).uniform_(-0.5 / self.embedding_size, 0.5 / self.embedding_size)]))
        self.ivectors.weight.requires_grad = True
        self.ovectors.weight.requires_grad = True

    def forward(self, data):
        return self.forward_i(data)

    def forward_i(self, data):
        # v = LT(data)
        v = data.long()
        v = v.cuda() if self.ivectors.weight.is_cuda else v
        return self.ivectors(v)

    def forward_o(self, data):
        # v = LT(data)
        v = data.long()
        v = v.cuda() if self.ovectors.weight.is_cuda else v
        return self.ovectors(v)


class SGNS(nn.Module):

    def __init__(self, embedding, vocab_size=20000, n_negs=20, weights=None):
        super(SGNS, self).__init__()
        self.embedding = embedding
        self.vocab_size = vocab_size
        self.n_negs = n_negs
        self.weights = None
        if weights is not None:
            wf = np.power(weights, 0.75)
            wf = wf / wf.sum()
            self.weights = FT(wf)

    def forward(self, iitem, oitems):
        batch_size = iitem.size()[0]
        context_size = oitems.size()[1]
        if self.weights is not None:
            nitems = t.multinomial(self.weights, batch_size * context_size * self.n_negs, replacement=True).view(batch_size, -1)
        else:
            nitems = FT(batch_size, context_size * self.n_negs).uniform_(0, self.vocab_size - 1).long()
        ivectors = self.embedding.forward_i(iitem).unsqueeze(2)
        # print('ivectors', ivectors.shape)
        ovectors = self.embedding.forward_o(oitems)
        # print('ovectors', ovectors.shape)
        nvectors = self.embedding.forward_o(nitems).neg()
        # print('nvectors', nvectors.shape)
        # print('mult o and i', t.bmm(ovectors, ivectors).shape)
        oloss = t.bmm(ovectors, ivectors).squeeze(dim=-1).sigmoid().log()
        # print('oloss', oloss.shape)
        assert oloss.shape[0] == batch_size, 'oloss vector shape is different than batch size'
        nloss = t.bmm(nvectors, ivectors).squeeze(dim=-1).sigmoid().log().view(-1, context_size, self.n_negs).sum(2)
        # print('mult n and i', t.bmm(nvectors, ivectors).shape)
        # print('nloss', nloss.shape)
        assert nloss.shape[0] == batch_size, 'nloss vector shape is different than batch size'
        loss = oloss + nloss
        loss = loss.sum(1).mean()
        return -loss