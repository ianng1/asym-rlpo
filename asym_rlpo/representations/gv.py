import math
from functools import cached_property
from typing import Dict, Iterable, List, Union

import gym
import gym.spaces
import torch
import torch.nn as nn

import asym_rlpo.generalized_torch as gtorch
from asym_rlpo.modules.mlp import make_mlp
from asym_rlpo.representations.cat import CatRepresentation
from asym_rlpo.representations.embedding import EmbeddingRepresentation
from asym_rlpo.utils.convert import numpy2torch
from asym_rlpo.utils.debugging import checkraise
import pdb 
from .base import Representation

# gridverse types
GV_State = Dict[str, torch.Tensor]
GV_Observation = Dict[str, torch.Tensor]


def _check_gv_observation_space_keys(space: gym.Space) -> bool:
    checkraise(
        isinstance(space, gym.spaces.Dict),
        TypeError,
        'incorrect observation space type',
    )

    for key in ['grid', 'item']:
        checkraise(
            key in space.spaces,
            KeyError,
            f'space does not contain `{key}` key',
        )


def _check_gv_state_space_keys(space: gym.Space) -> bool:
    checkraise(
        isinstance(space, gym.spaces.Dict),
        TypeError,
        'incorrect state space type',
    )

    for key in ['grid', 'agent_id_grid', 'agent', 'item']:
        checkraise(
            key in space.spaces,
            KeyError,
            f'space does not contain `{key}` key',
        )


class GV_Representation(Representation):
    def __init__(
        self,
        space: gym.spaces.Dict,
        names: Iterable[str],
        *,
        embedding_size: int,
        layers: List[int],
    ):
        super().__init__()
        self.space = space

        # USE THIS NORMALLY
        # num_embeddings = max(
        #     space['grid'].high.max() + 1,
        #     space['item'].high.max() + 1,
        # )

        num_embeddings = max([
            space[x].high.max() + 1 for x in space.keys()
        ])
        self.embedding = EmbeddingRepresentation(num_embeddings, embedding_size)
        gv_models = [self._make_gv_model(name) for name in names]
        self.cat_representation = CatRepresentation(gv_models)
        self.fc_model: nn.Module

        if len(layers) > 0:
            sizes = [self.cat_representation.dim] + layers
            nonlinearities = ['relu'] * len(layers)
            self.fc_model = make_mlp(sizes, nonlinearities)
            self._dim = sizes[-1]

        else:
            self.fc_model = nn.Identity()
            self._dim = self.cat_representation.dim

    @property
    def dim(self):
        return self._dim

    def forward(self, inputs: GV_State):
        return self.fc_model(self.cat_representation(inputs))

    def _make_gv_model(self, name: str):
        if name == 'agent':
            checkraise(
                'agent' in self.space.spaces,
                KeyError,
                'space does not contain `agent` key',
            )
            return GV_Agent_Representation(self.space)

        if name == 'item':
            checkraise(
                'item' in self.space.spaces,
                KeyError,
                'space does not contain `item` key',
            )
            return GV_Item_Representation(self.space, self.embedding)

        if name == 'grid-cnn':
            checkraise(
                'grid' in self.space.spaces,
                KeyError,
                'space does not contain `grid` key',
            )
            return GV_Grid_CNN_Representation(self.space, self.embedding)

        if name == 'grid-fc':
            checkraise(
                'grid' in self.space.spaces,
                KeyError,
                'space does not contain `grid` key',
            )
            return GV_Grid_FC_Representation(self.space, self.embedding)

        if name == 'agent-grid-cnn':
            checkraise(
                'grid' in self.space.spaces,
                KeyError,
                'space does not contain `grid` key',
            )
            checkraise(
                'agent_id_grid' in self.space.spaces,
                KeyError,
                'space does not contain `agent_id_grid` key',
            )
            return GV_AgentGrid_CNN_Representation(self.space, self.embedding)

        if name == 'agent-grid-fc':
            checkraise(
                'grid' in self.space.spaces,
                KeyError,
                'space does not contain `grid` key',
            )
            checkraise(
                'agent_id_grid' in self.space.spaces,
                KeyError,
                'space does not contain `agent_id_grid` key',
            )
            return GV_AgentGrid_FC_Representation(self.space, self.embedding)
        
        if name == 'map':
            return GV_Map_Representation(self.space)

        raise ValueError(f'invalid gv model name {name}')


def gv_cnn(in_channels):
    """Gridverse convolutional network shared by the observation/state representations."""
    return nn.Sequential(
        nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),
        nn.ReLU(),
        nn.Conv2d(32, 64, kernel_size=3, padding=1),
        nn.ReLU(),
        nn.Conv2d(64, 64, kernel_size=3, padding=1),
        nn.ReLU(),
    )


def batchify(gv_type: Union[GV_Observation, GV_State]):
    """Adds a batch axis to every subcomponent of a gridverse state or observation."""
    return gtorch.unsqueeze(gv_type, 0)


class GV_Agent_Representation(Representation):
    def __init__(self, space: gym.spaces.Dict):
        super().__init__()
        checkraise(
            'agent' in space.spaces,
            KeyError,
            'space does not contain `agent` key',
        )

        self.space = space

    @property
    def dim(self):
        (agent_dim,) = self.space['agent'].shape
        return agent_dim

    def forward(self, inputs: GV_Observation):
        agent = inputs['agent']
        return agent


class GV_Item_Representation(Representation):
    def __init__(
        self,
        space: gym.spaces.Dict,
        embedding: EmbeddingRepresentation,
    ):
        super().__init__()
        checkraise(
            'item' in space.spaces,
            KeyError,
            'space does not contain `item` key',
        )

        self.space = space
        self.embedding = embedding

    @property
    def dim(self):
        (item_dim,) = self.space['item'].shape
        return item_dim * self.embedding.dim

    def forward(self, inputs: GV_Observation):
        item = inputs['item']
        return self.embedding(item).flatten(start_dim=-2)


class GV_Grid_CNN_Representation(Representation):
    def __init__(
        self,
        space: gym.spaces.Dict,
        embedding: EmbeddingRepresentation,
    ):
        super().__init__()
        checkraise(
            'grid' in space.spaces,
            KeyError,
            'space does not contain `grid` key',
        )

        self.space = space
        self.embedding = embedding

        grid_channels = space.spaces['grid'].shape[-1]
        in_channels = grid_channels * embedding.dim
        self.cnn = gv_cnn(in_channels)

    @cached_property
    def dim(self):
        observation = self.space.sample()
        observation = batchify(numpy2torch(observation))
        return self.forward(observation).shape[1]

    def forward(self, inputs: GV_Observation):
        grid = inputs['grid']
        grid = self.embedding(grid).flatten(start_dim=-2)

        cnn_input = torch.transpose(grid, 1, 3)
        cnn_output = self.cnn(cnn_input)
        cnn_output = cnn_output.flatten(start_dim=1)

        return cnn_output


class GV_AgentGrid_CNN_Representation(Representation):
    def __init__(
        self,
        space: gym.spaces.Dict,
        embedding: EmbeddingRepresentation,
    ):
        super().__init__()
        checkraise(
            'grid' in space.spaces,
            KeyError,
            'space does not contain `grid` key',
        )
        checkraise(
            'agent_id_grid' in space.spaces,
            KeyError,
            'space does not contain `agent_id_grid` key',
        )

        self.space = space
        self.embedding = embedding

        grid_channels = space.spaces['grid'].shape[-1]
        # adding one for agent_id_grid
        in_channels = grid_channels * embedding.dim + 1
        self.cnn = gv_cnn(in_channels)

    @cached_property
    def dim(self):
        state = self.space.sample()
        state = batchify(numpy2torch(state))
        return self.forward(state).shape[1]

    def forward(self, inputs: GV_Observation):
        grid = inputs['grid']
        agent_id_grid = inputs['agent_id_grid']

        grid = self.embedding(grid).flatten(start_dim=-2)
        agent_id_grid = agent_id_grid.unsqueeze(-1)
        cnn_input = torch.cat([grid, agent_id_grid], dim=-1)
        cnn_input = torch.transpose(cnn_input, 1, 3)
        cnn_output = self.cnn(cnn_input)
        cnn_output = cnn_output.flatten(start_dim=1)

        return cnn_output


class GV_Grid_FC_Representation(Representation):
    def __init__(
        self,
        space: gym.spaces.Dict,
        embedding: EmbeddingRepresentation,
    ):
        super().__init__()
        checkraise(
            'grid' in space.spaces,
            KeyError,
            'space does not contain `grid` key',
        )

        self.space = space
        self.embedding = embedding

    @property
    def dim(self):
        grid_dim = math.prod(self.space['grid'].shape)
        return grid_dim * self.embedding.dim

    def forward(self, inputs: GV_Observation):
        grid = inputs['grid']
        return self.embedding(grid).flatten(start_dim=-4)


class GV_AgentGrid_FC_Representation(Representation):
    def __init__(
        self,
        space: gym.spaces.Dict,
        embedding: EmbeddingRepresentation,
    ):
        super().__init__()
        checkraise(
            'grid' in space.spaces,
            KeyError,
            'space does not contain `grid` key',
        )
        checkraise(
            'agent_id_grid' in space.spaces,
            KeyError,
            'space does not contain `agent_id_grid` key',
        )

        self.space = space
        self.embedding = embedding

    @property
    def dim(self):
        grid_dim = math.prod(self.space['grid'].shape)
        agent_dim = math.prod(self.space['agent_id_grid'].shape)
        return grid_dim * self.embedding.dim + agent_dim

    def forward(self, inputs: GV_Observation):
        grid = inputs['grid']
        agent_id_grid = inputs['agent_id_grid']

        grid = self.embedding(grid).flatten(start_dim=-4)
        agent_id_grid = agent_id_grid.flatten(start_dim=-2)
        return torch.cat([grid, agent_id_grid], dim=-1)


class GV_Memory_Representation(Representation):
    def __init__(self, space: gym.spaces.Box, *, embedding_size: int):
        super().__init__()
        num_embeddings = space.high.item() + 1
        self.embedding = EmbeddingRepresentation(num_embeddings, embedding_size)

    @property
    def dim(self):
        return self.embedding.dim

    def forward(self, inputs: torch.Tensor):
        return self.embedding(inputs)

class GV_Map_Representation(Representation):
    def __init__(self, space: gym.spaces.Box):
        super().__init__()

    @property
    def dim(self):
        return 3

    def forward(self, inputs: torch.Tensor):
        # return self.embedding(inputs)
        print(inputs)
        inputs = inputs['grid'][0][0][0][0]

        # split into 3-tuple
        return torch.tensor([inputs // 100, (inputs % 100) // 10, (inputs % 10)])