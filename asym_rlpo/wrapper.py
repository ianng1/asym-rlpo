from typing import List

import gym
import gym.spaces
import numpy as np

from asym_rlpo.utils.debugging import checkraise


class IndexWrapper(gym.ObservationWrapper):
    """IndexWrapper.

    Takes a gym.Env with a flat Box observation space, and filters such that
    only the dimensions indicated by `indices` are observable.
    """

    def __init__(self, env: gym.Env, indices: List[int]):
        checkraise(
            isinstance(env.observation_space, gym.spaces.Box)
            and len(env.observation_space.shape) == 1,
            ValueError,
            'env.observation_space must be flat Box',
        )

        checkraise(
            len(set(indices)) == len(indices),
            ValueError,
            'indices must be unique',
        )

        assert isinstance(env.observation_space, gym.spaces.Box)
        checkraise(
            len(indices) <= env.observation_space.shape[0],
            ValueError,
            'number of indices must not exceed state dimensions',
        )

        checkraise(
            min(indices) >= 0,
            ValueError,
            'indices must be non-negative',
        )

        checkraise(
            max(indices) < env.observation_space.shape[0],
            ValueError,
            'indices must be lower than state dimensions',
        )

        super().__init__(env)

        self._indices = indices
        self.state_space = env.observation_space
        self.observation_space = gym.spaces.Box(
            env.observation_space.low[indices],
            env.observation_space.high[indices],
        )

        self.state: np.ndarray

    def observation(self, observation):
        self.state = observation
        return observation[self._indices]
