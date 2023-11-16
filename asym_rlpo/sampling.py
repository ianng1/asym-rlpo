from typing import List, Optional

import torch

from asym_rlpo.envs import Environment
from asym_rlpo.policies import Policy, RandomPolicy
from asym_rlpo.utils.convert import numpy2torch

from .data import Episode, Interaction
import pdb

def sample_episode(
    env: Environment,
    policy: Optional[Policy] = None,
    *,
    render: bool = False,
    filepath: str = "",
) -> Episode:
    actions = []
    if policy is None:
        policy = RandomPolicy(env.action_space)
    with torch.no_grad():
        interactions: List[Interaction] = []
        counter = 0
        done = False
        observation, latent = env.reset()
        policy.reset(numpy2torch(observation))
        if render:
            env.render()
        while True:
            action = policy.sample_action()
            actions.append(int(action))
            next_observation, next_latent, reward, done = env.step(action)
            policy.step(torch.tensor(action), numpy2torch(next_observation))
            if render:
                env.render()

            interactions.append(
                Interaction(
                    observation=observation,
                    latent=latent,
                    action=action,
                    reward=reward,
                )
            )

            if done:
                break

            latent = next_latent
            observation = next_observation
            counter += 1
    if render:
        with open(filepath, 'w') as txtfile:
            txtfile.write(" ".join(actions))
    return Episode.from_interactions(interactions)


def sample_episodes(
    env: Environment,
    policy: Optional[Policy] = None,
    *,
    num_episodes: int,
    render: bool = False,
) -> List[Episode]:
    if policy is None:
        policy = RandomPolicy(env.action_space)

    return [
        sample_episode(env, policy, render=render) for _ in range(num_episodes)
    ]
