import functools
from typing import Optional, Type

from asym_rlpo.envs import Environment
from asym_rlpo.features import compute_history_features, make_history_integrator
from asym_rlpo.models import make_models

from .a2c.a2c import A2C
from .a2c.asym_a2c import AsymA2C
from .a2c.asym_a2c_state import AsymA2C_State
from .a2c.base import A2C_ABC
from .dqn.adqn import ADQN, ADQN_Bootstrap
from .dqn.adqn_short import ADQN_Short
from .dqn.adqn_state import ADQN_State, ADQN_State_Bootstrap
from .dqn.base import DQN_ABC
from .dqn.dqn import DQN
import pdb

_a2c_algorithm_classes = {
    'a2c': A2C,
    'asym-a2c': AsymA2C,
    'asym-a2c-state': AsymA2C_State,
}

_dqn_algorithm_classes = {
    'dqn': DQN,
    'adqn': ADQN,
    'adqn-bootstrap': ADQN_Bootstrap,
    'adqn-state': ADQN_State,
    'adqn-state-bootstrap': ADQN_State_Bootstrap,
    'adqn-short': ADQN_Short,
}


def get_a2c_algorithm_class(name: str) -> Type[A2C_ABC]:
    try:
        return _a2c_algorithm_classes[name]
    except KeyError:
        raise ValueError(f'invalid algorithm name {name}')


def get_dqn_algorithm_class(name: str) -> Type[DQN_ABC]:
    try:
        return _dqn_algorithm_classes[name]
    except KeyError:
        raise ValueError(f'invalid algorithm name {name}')


def make_a2c_algorithm(
    name: str,
    env: Environment,
    *,
    truncated_histories_n: Optional[int] = None,
) -> A2C_ABC:
    partial_make_history_integrator = functools.partial(
        make_history_integrator,
        truncated_histories_n=truncated_histories_n,
    )
    partial_compute_history_features = functools.partial(
        compute_history_features,
        n=truncated_histories_n,
    )

    algorithm_class = get_a2c_algorithm_class(name)
    models = make_models(env, keys=algorithm_class.model_keys)
    return algorithm_class(
        models,
        make_history_integrator=partial_make_history_integrator,
        compute_history_features=partial_compute_history_features,
    )


def make_dqn_algorithm(
    name: str,
    env: Environment,
    *,
    truncated_histories_n: Optional[int] = None,
) -> DQN_ABC:
    partial_make_history_integrator = functools.partial(
        make_history_integrator,
        truncated_histories_n=truncated_histories_n,
    )
    partial_compute_history_features = functools.partial(
        compute_history_features,
        n=truncated_histories_n,
    )
    
    algorithm_class = get_dqn_algorithm_class(name)
    models = make_models(env, keys=algorithm_class.model_keys)
    # pdb.set_trace()
    return algorithm_class(
        models,
        make_history_integrator=partial_make_history_integrator,
        compute_history_features=partial_compute_history_features,
    )
