from __future__ import annotations

import random
from typing import Sequence

import gym
import torch
import torch.nn as nn
import torch.nn.functional as F

from asym_rlpo.data import Episode
from asym_rlpo.models import make_models
from asym_rlpo.policies.base import PartiallyObservablePolicy

from .base import EpisodicDQN


class POE_ADQN(EpisodicDQN):
    def make_models(self, env: gym.Env) -> nn.ModuleDict:
        return make_models(env)

    def target_policy(self) -> TargetPolicy:
        return TargetPolicy(self.models)

    def behavior_policy(
        self, action_space: gym.spaces.Discrete
    ) -> BehaviorPolicy:
        return BehaviorPolicy(self.models, action_space)

    def episodic_loss(
        self, episodes: Sequence[Episode], *, discount: float
    ) -> torch.Tensor:
        def compute_q_values(models, actions, observations, states):
            action_features = models.action_model(actions)
            action_features = action_features.roll(1, 0)
            action_features[0, :] = 0.0
            observation_features = models.observation_model(observations)

            inputs = torch.cat([action_features, observation_features], dim=-1)
            history_features, _ = models.history_model(inputs.unsqueeze(0))
            history_features = history_features.squeeze(0)
            qh_values = models.qh_model(history_features)

            state_features = models.state_model(states)
            inputs = torch.cat([history_features, state_features], dim=-1)
            qhs_values = models.qhs_model(inputs)

            return qh_values, qhs_values

        def qhs_loss(
            episode,
            qh_values,
            qhs_values,
            target_qh_values,
            target_qhs_values,
        ) -> torch.Tensor:

            qhs_values = qhs_values.gather(
                1, episode.actions.unsqueeze(-1)
            ).squeeze(-1)
            qhs_values_bootstrap = torch.tensor(0.0).where(
                episode.dones,
                target_qhs_values.gather(
                    1, target_qh_values.argmax(-1).unsqueeze(-1)
                )
                .squeeze(-1)
                .roll(-1, 0),
            )

            loss = F.mse_loss(
                qhs_values,
                episode.rewards + discount * qhs_values_bootstrap,
            )
            return loss

        def qh_loss(
            episode,
            qh_values,
            qhs_values,
            target_qh_values,
            target_qhs_values,
        ) -> torch.Tensor:
            # loss = F.mse_loss(qh_values, target_qhs_values)
            # return loss

            # qh_values = qh_values.gather(1, episode.actions.unsqueeze(-1)).squeeze(
            #     -1
            # )
            # target_qhs_values = target_qhs_values.gather(
            #     1, episode.actions.unsqueeze(-1)
            # ).squeeze(-1)
            # loss = F.mse_loss(qh_values, target_qhs_values)
            # return loss

            qh_values = qh_values.gather(
                1, episode.actions.unsqueeze(-1)
            ).squeeze(-1)
            qhs_values_bootstrap = torch.tensor(0.0).where(
                episode.dones,
                target_qhs_values.gather(
                    1, target_qh_values.argmax(-1).unsqueeze(-1)
                )
                .squeeze(-1)
                .roll(-1, 0),
            )

            loss = F.mse_loss(
                qh_values,
                episode.rewards + discount * qhs_values_bootstrap,
            )
            return loss

        losses = []
        for episode in episodes:

            qh_values, qhs_values = compute_q_values(
                self.models,
                episode.actions,
                episode.observations,
                episode.states,
            )
            with torch.no_grad():
                target_qh_values, target_qhs_values = compute_q_values(
                    self.target_models,
                    episode.actions,
                    episode.observations,
                    episode.states,
                )

            loss = (
                qhs_loss(
                    episode,
                    qh_values,
                    qhs_values,
                    target_qh_values,
                    target_qhs_values,
                )
                + qh_loss(
                    episode,
                    qh_values,
                    qhs_values,
                    target_qh_values,
                    target_qhs_values,
                )
            ) / 2

            losses.append(loss)

        return sum(losses, start=torch.tensor(0.0)) / len(losses)
        # return sum(losses, start=torch.tensor(0.0)) / sum(
        #     len(episode) for episode in episodes
        # )


class TargetPolicy(PartiallyObservablePolicy):
    def __init__(self, models: nn.ModuleDict):
        super().__init__()
        self.models = models

        self.history_features = None
        self.hidden = None

    def reset(self, observation):
        action_features = torch.zeros(self.models.action_model.dim)
        observation_features = self.models.observation_model(observation)
        self._update(action_features, observation_features)

    def step(self, action, observation):
        action_features = self.models.action_model(action)
        observation_features = self.models.observation_model(observation)
        self._update(action_features, observation_features)

    def _update(self, action_features, observation_features):
        input_features = (
            torch.cat([action_features, observation_features])
            .unsqueeze(0)
            .unsqueeze(0)
        )
        self.history_features, self.hidden = self.models.history_model(
            input_features, hidden=self.hidden
        )
        self.history_features = self.history_features.squeeze(0).squeeze(0)

    def po_sample_action(self):
        q_values = self.models.qh_model(self.history_features)
        return q_values.argmax().item()


class BehaviorPolicy(PartiallyObservablePolicy):
    def __init__(self, models: nn.ModuleDict, action_space: gym.Space):
        super().__init__()
        self.target_policy = TargetPolicy(models)
        self.action_space = action_space
        self.epsilon: float

    def reset(self, observation):
        self.target_policy.reset(observation)

    def step(self, action, observation):
        self.target_policy.step(action, observation)

    def po_sample_action(self):
        return (
            self.action_space.sample()
            if random.random() < self.epsilon
            else self.target_policy.po_sample_action()
        )
