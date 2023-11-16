#!/usr/bin/env python
import argparse
import logging
import logging.config
import random
import signal
from dataclasses import asdict, dataclass
from typing import Dict, NamedTuple

import pdb
import numpy as np
import torch
import torch.nn as nn
import wandb
from gym_gridverse.rng import reset_gv_rng

from asym_rlpo.algorithms import make_dqn_algorithm
from asym_rlpo.algorithms.dqn.base import DQN_ABC
from asym_rlpo.data import EpisodeBuffer
from asym_rlpo.envs import Environment, LatentType, make_env
from asym_rlpo.evaluation import evaluate_returns
from asym_rlpo.policies import Policy, RandomPolicy
from asym_rlpo.sampling import sample_episode, sample_episodes
from asym_rlpo.utils.checkpointing import Serializer, load_data, save_data
from asym_rlpo.utils.config import get_config
from asym_rlpo.utils.device import get_device
from asym_rlpo.utils.dispenser import (
    DiscreteDispenser,
    DiscreteDispenserSerializer,
    TimeDispenser,
)
from asym_rlpo.utils.running_average import (
    InfiniteRunningAverage,
    RunningAverage,
    RunningAverageSerializer,
    WindowRunningAverage,
)
from asym_rlpo.utils.scheduling import make_schedule
from asym_rlpo.utils.timer import Timer, TimerSerializer
from asym_rlpo.utils.wandb_logger import WandbLogger, WandbLoggerSerializer

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser()

    # wandb arguments
    parser.add_argument('--wandb-entity', default='iyhn8192')
    parser.add_argument('--wandb-project', default=None)
    parser.add_argument('--wandb-group', default=None)
    parser.add_argument('--wandb-tag', action='append', dest='wandb_tags')
    parser.add_argument('--wandb-offline', action='store_true')

    # wandb related
    parser.add_argument('--num-wandb-logs', type=int, default=200)

    # algorithm and environment
    parser.add_argument('env')
    parser.add_argument(
        'algo',
        choices=[
            'dqn',
            'adqn',
            'adqn-bootstrap',
            'adqn-short',
            'adqn-state',
            'adqn-state-bootstrap',
        ],
    )

    parser.add_argument('--env-label', default=None)
    parser.add_argument('--algo-label', default=None)

    # truncated histories
    parser.add_argument(
        '--history-model',
        choices=['rnn', 'gru', 'attention'],
        default='gru',
    )
    parser.add_argument('--truncated-histories-n', type=int, default=None)

    # reproducibility
    parser.add_argument('--seed', type=int, default=None)
    parser.add_argument('--deterministic', action='store_true')

    # general
    parser.add_argument(
        '--max-simulation-timesteps', type=int, default=2_000_000
    )
    parser.add_argument('--max-episode-timesteps', type=int, default=20)
    parser.add_argument('--simulation-num-episodes', type=int, default=1)

    # evaluation
    parser.add_argument('--evaluation', action='store_true')
    parser.add_argument('--evaluation-period', type=int, default=10)
    parser.add_argument('--evaluation-num-episodes', type=int, default=1)

    # discounts
    parser.add_argument('--evaluation-discount', type=float, default=1.0)
    parser.add_argument('--training-discount', type=float, default=0.99)

    # episode buffer
    parser.add_argument(
        '--episode-buffer-max-timesteps', type=int, default=1_000_000
    )
    parser.add_argument(
        '--episode-buffer-prepopulate-timesteps', type=int, default=320_000
    )
    parser.add_argument(
        '--episode-buffer-prepopulate-policy',
        choices=['random', 'behavior', 'target'],
        default='behavior',
    )

    # target
    parser.add_argument('--target-update-period', type=int, default=10_000)

    # training parameters
    parser.add_argument(
        '--training-timesteps-per-simulation-timestep', type=int, default=8
    )
    parser.add_argument('--training-num-episodes', type=int, default=1)

    # epsilon schedule
    parser.add_argument('--epsilon-schedule', default='linear')
    parser.add_argument('--epsilon-value-from', type=float, default=1.0)
    parser.add_argument('--epsilon-value-to', type=float, default=0.1)
    parser.add_argument('--epsilon-nsteps', type=int, default=1_000_000)

    # optimization
    parser.add_argument('--optim-lr', type=float, default=1e-4)
    parser.add_argument('--optim-eps', type=float, default=1e-4)
    parser.add_argument('--optim-max-norm', type=float, default=float('inf'))

    # device
    parser.add_argument('--device', default='auto')

    parser.add_argument('--render', action='store_true')

    # temporary / development
    parser.add_argument('--hs-features-dim', type=int, default=0)
    parser.add_argument('--normalize-hs-features', action='store_true')

    # latent observation
    parser.add_argument('--latent-type', default='state')

    # representation options
    parser.add_argument(
        '--attention-num-heads',
        choices=[2**k for k in range(10)],
        type=int,
        default=2,
    )

    # gv models
    parser.add_argument('--gv-observation-representation', default='compact')
    parser.add_argument('--gv-state-representation', default='compact')

    parser.add_argument(
        '--gv-observation-grid-model-type',
        choices=['cnn', 'fc'],
        default='fc',
    )
    parser.add_argument(
        '--gv-observation-representation-layers',
        type=int,
        default=0,
    )

    parser.add_argument(
        '--gv-state-grid-model-type',
        choices=['cnn', 'fc'],
        default='fc',
    )
    parser.add_argument(
        '--gv-state-representation-layers',
        type=int,
        default=0,
    )

    # checkpoint
    parser.add_argument('--checkpoint', default=None)
    parser.add_argument('--checkpoint-period', type=int, default=36_000)

    parser.add_argument('--save-model', action='store_true')
    parser.add_argument('--model-filename', default=None)

    parser.add_argument('--save-modelseq', action='store_true')
    parser.add_argument('--modelseq-filename', default=None)

    args = parser.parse_args()
    args.env_label = args.env if args.env_label is None else args.env_label
    args.algo_label = args.algo if args.algo_label is None else args.algo_label
    args.wandb_mode = 'offline' if args.wandb_offline else None
    return args


@dataclass
class XStats:
    epoch: int = 0
    simulation_episodes: int = 0
    simulation_timesteps: int = 0
    optimizer_steps: int = 0
    training_episodes: int = 0
    training_timesteps: int = 0

    def asdict(self):
        return asdict(self)


class XStatsSerializer(Serializer[XStats]):
    def serialize(self, obj: XStats) -> Dict:
        return obj.asdict()

    def deserialize(self, obj: XStats, data: Dict):
        obj.epoch = data['epoch']
        obj.simulation_episodes = data['simulation_episodes']
        obj.simulation_timesteps = data['simulation_timesteps']
        obj.optimizer_steps = data['optimizer_steps']
        obj.training_episodes = data['training_episodes']
        obj.training_timesteps = data['training_timesteps']


class RunState(NamedTuple):
    env: Environment
    algo: DQN_ABC
    optimizer: torch.optim.Optimizer
    wandb_logger: WandbLogger
    xstats: XStats
    timer: Timer
    running_averages: Dict[str, RunningAverage]
    dispensers: Dict[str, DiscreteDispenser]


class RunStateSerializer(Serializer[RunState]):
    def __init__(self):
        self.wandb_logger_serializer = WandbLoggerSerializer()
        self.xstats_serializer = XStatsSerializer()
        self.timer_serializer = TimerSerializer()
        self.running_average_serializer = RunningAverageSerializer()
        self.dispenser_serializer = DiscreteDispenserSerializer()

    def serialize(self, obj: RunState) -> Dict:
        return {
            'models': obj.algo.models.state_dict(),
            'target_models': obj.algo.target_models.state_dict(),
            'optimizer': obj.optimizer.state_dict(),
            'wandb_logger': self.wandb_logger_serializer.serialize(
                obj.wandb_logger
            ),
            'xstats': self.xstats_serializer.serialize(obj.xstats),
            'timer': self.timer_serializer.serialize(obj.timer),
            'running_averages': {
                k: self.running_average_serializer.serialize(v)
                for k, v in obj.running_averages.items()
            },
            'dispensers': {
                k: self.dispenser_serializer.serialize(v)
                for k, v in obj.dispensers.items()
            },
        }

    def deserialize(self, obj: RunState, data: Dict):
        obj.algo.models.load_state_dict(data['models'])
        obj.algo.target_models.load_state_dict(data['target_models'])
        obj.optimizer.load_state_dict(data['optimizer'])
        self.wandb_logger_serializer.deserialize(
            obj.wandb_logger,
            data['wandb_logger'],
        )
        self.xstats_serializer.deserialize(obj.xstats, data['xstats'])
        self.timer_serializer.deserialize(obj.timer, data['timer'])

        data_keys = data['running_averages'].keys()
        obj_keys = obj.running_averages.keys()
        if set(data_keys) != set(obj_keys):
            raise RuntimeError()
        for k, running_average in obj.running_averages.items():
            self.running_average_serializer.deserialize(
                running_average,
                data['running_averages'][k],
            )

        data_keys = data['dispensers'].keys()
        obj_keys = obj.dispensers.keys()
        if set(data_keys) != set(obj_keys):
            raise RuntimeError()
        for k, dispenser in obj.dispensers.items():
            self.dispenser_serializer.deserialize(
                dispenser,
                data['dispensers'][k],
            )


def setup() -> RunState:
    config = get_config()
    table = str.maketrans({'-': '_'})
    latent_type = LatentType[config.latent_type.upper().translate(table)]
    
    env = make_env(
        config.env,
        latent_type=latent_type,
        max_episode_timesteps=config.max_episode_timesteps,
    )
    algo = make_dqn_algorithm(
        config.algo,
        env,
        truncated_histories_n=config.truncated_histories_n,
    )
    optimizer = torch.optim.Adam(
        algo.models.parameters(),
        lr=config.optim_lr,
        eps=config.optim_eps,
    )

    wandb_logger = WandbLogger()

    xstats = XStats()
    timer = Timer()

    running_averages = {
        'avg_target_returns': InfiniteRunningAverage(),
        'avg_behavior_returns': InfiniteRunningAverage(),
        'avg100_behavior_returns': WindowRunningAverage(100),
    }

    wandb_log_period = config.max_simulation_timesteps // config.num_wandb_logs
    dispensers = {
        'target_update_dispenser': DiscreteDispenser(
            config.target_update_period
        ),
        'wandb_log_dispenser': DiscreteDispenser(wandb_log_period),
    }

    return RunState(
        env,
        algo,
        optimizer,
        wandb_logger,
        xstats,
        timer,
        running_averages,
        dispensers,
    )


def save_checkpoint(runstate: RunState):
    """saves a checkpoint with the current runstate

    NOTE:  must be called within an active wandb.init context manager
    """
    config = get_config()

    if config.checkpoint is not None:
        assert wandb.run is not None

        logger.info('checkpointing...')
        runstate_serializer = RunStateSerializer()
        checkpoint = {
            'metadata': {
                'config': config._as_dict(),
                'wandb_id': wandb.run.id,
            },
            'data': runstate_serializer.serialize(runstate),
        }
        save_data(config.checkpoint, checkpoint)
        logger.info('checkpointing DONE')


def run(runstate: RunState) -> bool:
    config = get_config()
    logger.info('run %s %s', config.env_label, config.algo_label)

    (
        env,
        algo,
        optimizer,
        wandb_logger,
        xstats,
        timer,
        running_averages,
        dispensers,
    ) = runstate

    avg_target_returns = running_averages['avg_target_returns']
    avg_behavior_returns = running_averages['avg_behavior_returns']
    avg100_behavior_returns = running_averages['avg100_behavior_returns']
    target_update_dispenser = dispensers['target_update_dispenser']
    wandb_log_dispenser = dispensers['wandb_log_dispenser']

    device = get_device(config.device)
    algo.to(device)

    # reproducibility
    if config.seed is not None:
        random.seed(config.seed)
        np.random.seed(config.seed)
        torch.manual_seed(config.seed)
        reset_gv_rng(config.seed)
        env.seed(config.seed)

    if config.deterministic:
        torch.use_deterministic_algorithms(True)

    epsilon_schedule = make_schedule(
        config.epsilon_schedule,
        value_from=config.epsilon_value_from,
        value_to=config.epsilon_value_to,
        nsteps=config.epsilon_nsteps,
    )
    behavior_policy = algo.behavior_policy(env.action_space)
    target_policy = algo.target_policy()

    logger.info(
        f'setting prepopulating policy:'
        f' {config.episode_buffer_prepopulate_policy}'
    )
    prepopulate_policy: Policy
    if config.episode_buffer_prepopulate_policy == 'random':
        prepopulate_policy = RandomPolicy(env.action_space)
    elif config.episode_buffer_prepopulate_policy == 'behavior':
        prepopulate_policy = behavior_policy
    elif config.episode_buffer_prepopulate_policy == 'target':
        prepopulate_policy = target_policy
    else:
        assert False

    if xstats.simulation_timesteps == 0:
        prepopulate_timesteps = config.episode_buffer_prepopulate_timesteps
    else:
        prepopulate_policy.epsilon = epsilon_schedule(
            xstats.simulation_timesteps
            - config.episode_buffer_prepopulate_timesteps
        )
        prepopulate_timesteps = xstats.simulation_timesteps

    # instantiate and prepopulate buffer
    logger.info(
        f'prepopulating episode buffer'
        f' ({prepopulate_timesteps:_} timesteps)...'
    )
    episode_buffer = EpisodeBuffer(config.episode_buffer_max_timesteps)
    while episode_buffer.num_interactions() < prepopulate_timesteps:
        episode = sample_episode(env, prepopulate_policy)
        episode_buffer.append_episode(episode.torch())
        logger.debug(
            f'episode buffer {episode_buffer.num_interactions():_} timesteps'
        )
    logger.info('prepopulating DONE')

    if xstats.simulation_timesteps == 0:
        xstats.simulation_episodes = episode_buffer.num_episodes()
        xstats.simulation_timesteps = episode_buffer.num_interactions()

    # setup interrupt flag via signal
    interrupt = False

    def set_interrupt_flag():
        nonlocal interrupt
        logger.debug('signal received, setting interrupt=True')
        interrupt = True

    signal.signal(signal.SIGUSR1, lambda signal, frame: set_interrupt_flag())

    checkpoint_dispenser = TimeDispenser(config.checkpoint_period)
    checkpoint_dispenser.dispense()  # burn first dispense
    # main learning loop
    wandb.watch(algo.models)
    while xstats.simulation_timesteps < config.max_simulation_timesteps:
        if interrupt:
            break

        if checkpoint_dispenser.dispense():
            save_checkpoint(runstate)
        algo.models.eval()
        # evaluate target policy
        if config.evaluation and xstats.epoch % config.evaluation_period == 0:
            if config.render:
                sample_episode(
                    env,
                    target_policy,
                    render=True,
                )

            episodes = sample_episodes(
                env,
                target_policy,
                num_episodes=config.evaluation_num_episodes,
            )
            mean_length = sum(map(len, episodes)) / len(episodes)
            returns = evaluate_returns(
                episodes, discount=config.evaluation_discount
            )
            avg_target_returns.extend(returns.tolist())
            logger.info(
                'EVALUATE epoch %d simulation_step %d return %.3f',
                xstats.epoch,
                xstats.simulation_timesteps,
                returns.mean(),
            )
            print(
                'EVALUATE epoch %d simulation_step %d return %.3f',
                xstats.epoch,
                xstats.simulation_timesteps,
                returns.mean(),
            )
            wandb_logger.log(
                {
                    **xstats.asdict(),
                    'hours': timer.hours,
                    'diagnostics/target_mean_episode_length': mean_length,
                    'performance/target_mean_return': returns.mean(),
                    'performance/avg_target_mean_return': avg_target_returns.value(),
                }
            )

        # populate episode buffer
        behavior_policy.epsilon = epsilon_schedule(
            xstats.simulation_timesteps
            - config.episode_buffer_prepopulate_timesteps
        )
        episodes = sample_episodes(
            env,
            behavior_policy,
            num_episodes=config.simulation_num_episodes,
        )

        mean_length = sum(map(len, episodes)) / len(episodes)
        returns = evaluate_returns(
            episodes, discount=config.evaluation_discount
        )
        avg_behavior_returns.extend(returns.tolist())
        avg100_behavior_returns.extend(returns.tolist())

        wandb_log = wandb_log_dispenser.dispense(xstats.simulation_timesteps)

        if wandb_log:
            logger.info(
                'behavior log - simulation_step %d return %.3f',
                xstats.simulation_timesteps,
                returns.mean(),
            )
            wandb_logger.log(
                {
                    **xstats.asdict(),
                    'hours': timer.hours,
                    'diagnostics/epsilon': behavior_policy.epsilon,
                    'diagnostics/behavior_mean_episode_length': mean_length,
                    'performance/behavior_mean_return': returns.mean(),
                    'performance/avg_behavior_mean_return': avg_behavior_returns.value(),
                    'performance/avg100_behavior_mean_return': avg100_behavior_returns.value(),
                }
            )

        # storing torch data directly
        episodes = [episode.torch().to(device) for episode in episodes]
        episode_buffer.append_episodes(episodes)
        xstats.simulation_episodes += len(episodes)
        


        prev_timesteps = xstats.simulation_timesteps
        xstats.simulation_timesteps += sum(len(episode) for episode in episodes)
        if (xstats.simulation_timesteps // 100000 != prev_timesteps // 100000):
            with open('/iris/u/iyhn8192/asym-rlpo/experiments/tigerdoor.txt', 'a') as txtfile:
                txtfile.write(" ".join([str(int(x)) for x in episodes[0].actions]) + "\n")
                print("Wrote new episode at timestep", prev_timesteps)
        # target model update
        if target_update_dispenser.dispense(xstats.simulation_timesteps):
            # Update the target network
            algo.target_models.load_state_dict(algo.models.state_dict())

        # train based on episode buffer
        algo.models.train()
        total_episodes = 0
        while (
            xstats.training_timesteps
            < (
                xstats.simulation_timesteps
                - config.episode_buffer_prepopulate_timesteps
            )
            * config.training_timesteps_per_simulation_timestep
        ):
            optimizer.zero_grad()

            episodes = episode_buffer.sample_episodes(
                config.training_num_episodes,
                replacement=True,
            )
            episodes = [episode.to(device) for episode in episodes]
            loss = algo.episodic_loss(
                episodes, discount=config.training_discount
            )
            loss.backward()
            gradient_norm = nn.utils.clip_grad.clip_grad_norm_(
                algo.models.parameters(), max_norm=config.optim_max_norm
            )
            optimizer.step()

            if wandb_log:
                logger.debug(
                    'training log - simulation_step %d loss %.3f',
                    xstats.simulation_timesteps,
                    loss,
                )
                wandb_logger.log(
                    {
                        **xstats.asdict(),
                        'hours': timer.hours,
                        'training/loss': loss,
                        'training/gradient_norm': gradient_norm,
                    }
                )

            if config.save_modelseq and config.modelseq_filename is not None:
                data = {
                    'metadata': config._as_dict(),
                    'data': {
                        'timestep': xstats.simulation_timesteps,
                        'model.state_dict': algo.models.state_dict(),
                    },
                }
                filename = config.modelseq_filename.format(
                    xstats.simulation_timesteps
                )
                save_data(filename, data)
            

            xstats.optimizer_steps += 1
            xstats.training_episodes += len(episodes)
            xstats.training_timesteps += sum(
                len(episode) for episode in episodes
            )

        xstats.epoch += 1

    done = not interrupt
    
    if (xstats.training_timesteps // 100000) != sum(len(episode) for episode in episodes) // 100000 and config.save_model and config.model_filename is not None:
        config_dict = config._as_dict()
        config_dict['wandb_id'] = wandb.run.id
        data = {
            'metadata': config_dict,
            'data': {'models.state_dict': algo.models.state_dict()},
        }
    save_data(config.model_filename + str(xstats.training_timesteps) + ".pt", data)
    if done and config.save_model and config.model_filename is not None:
        config_dict = config._as_dict()
        config_dict['wandb_id'] = wandb.run.id
        data = {
            'metadata': {'config': config_dict},
            'data': {'models.state_dict': algo.models.state_dict()},
        }
        save_data(config.model_filename + "final.pt", data)

    return done


def main() -> int:
    args = parse_args()
    wandb_kwargs = {
        'project': args.wandb_project,
        'entity': args.wandb_entity,
        'group': args.wandb_group,
        'tags': args.wandb_tags,
        'mode': args.wandb_mode,
        'config': args,
    }

    try:
        checkpoint = load_data(args.checkpoint)
    except (TypeError, FileNotFoundError):
        checkpoint = None
    else:
        wandb_kwargs.update(
            {
                'resume': 'must',
                'id': checkpoint['metadata']['wandb_id'],
            }
        )

    with wandb.init(**wandb_kwargs):
        config = get_config()
        config._update(dict(wandb.config))

        logger.info('setup of runstate...')
        runstate = setup()
        logger.info('setup DONE')

        if checkpoint is not None:
            if checkpoint['metadata']['config'] != config._as_dict():
                raise RuntimeError(
                    'checkpoint config inconsistent with program config'
                )

            logger.debug('updating runstate from checkpoint')
            runstate_serializer = RunStateSerializer()
            runstate_serializer.deserialize(runstate, checkpoint['data'])

        logger.info('run...')
        done = run(runstate)
        logger.info('run DONE')

        save_checkpoint(runstate)

    return int(not done)


if __name__ == '__main__':
    logging.config.dictConfig(
        {
            'version': 1,
            'disable_existing_loggers': False,
            'formatters': {
                'standard': {
                    'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
                },
            },
            'handlers': {
                'default_handler': {
                    'class': 'logging.StreamHandler',
                    'level': 'DEBUG',
                    'formatter': 'standard',
                    'stream': 'ext://sys.stdout',
                },
            },
            'loggers': {
                '': {
                    'handlers': ['default_handler'],
                    'level': 'DEBUG',
                    'propagate': False,
                }
            },
        }
    )

    raise SystemExit(main())
