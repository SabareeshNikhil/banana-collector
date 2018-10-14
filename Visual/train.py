import numpy as np
import argparse
from tqdm import trange
import sys
import os
import logging

from model import QNetwork
from visual_env import VisualEnvironment
from badaii.agents.dbl_dqn import Agent
from badaii import helpers
from q_metric import define_Q_metric, QMetric

import pdb 

ACTION_SIZE = 4 
SEED = 0

# Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

def log(info):
    print()
    logger.info(info)

# Helpers

def save(agent, out_file, ep, it, avg_scores, scores, q_metrics, last_saved_score):
    log('Saving agent...')
    params = { 
        'episodes': ep,
        'it': it,
        'avg_scores': avg_scores, 
        'scores': scores,
        'q_metrics': q_metrics,
        'last_saved_score': last_saved_score
        }
    agent.save(out_file, run_params=params)

def evaluate_policy(env, agent, episodes=100, steps=2000, eps=0.05):
    scores = []
    for _ in range(episodes):
        score = 0
        state = env.reset()
        for _ in range(steps):
            action = agent.act(state, epsilon=eps)
            state, reward, done = env.step(action)
            score += reward
            if done:
                break
        scores.append(score)
    return np.mean(scores)

# https://stackoverflow.com/questions/31447442/difference-between-os-execl-and-os-execv-in-python
def reload_process():
    if '--restore' not in sys.argv:
        sys.argv.append('--restore')
        sys.argv.append(None)
    idx = sum( [ i if arg=='--restore' else 0 for i, arg in enumerate(sys.argv)] )
    sys.argv[idx+1] = 'reload.ckpt'
    os.execv(sys.executable, ['python', __file__, *sys.argv[1:]])

# Train 
def train(episodes=2000, steps=2000, env_file='data/Banana_x86_x64',
          out_file=None, restore=None, from_start=True, 
          reload_every=1000, log_every=10, action_repeat=4, update_frequency=1, 
          batch_size=32, gamma=0.99,lrate=2.5e-4, tau=0.05,
          replay_mem_size=100000, replay_start_size=5000, 
          ini_eps=1.0, final_eps=0.1, final_exp_it=200000, save_thresh=5.0):
    """Train Double DQN
    
    Args:
      episodes (int): Number of episodes to run 
      steps (int): Maximum number of steps per episode
      env_file (str): Path to environment file
    
    Returns:
        None
    """
    # Define agent 
    log('Creating agent...')
    m = QNetwork(action_repeat, ACTION_SIZE, SEED)
    m_t = QNetwork(action_repeat, ACTION_SIZE, SEED)
    
    agent = Agent(
        m, m_t,
        action_size=ACTION_SIZE, 
        seed=SEED,
        batch_size=batch_size,
        gamma = gamma,
        update_frequency = update_frequency,
        lrate = lrate,
        replay_size = replay_mem_size,
        tau = tau,
        restore = restore
    )

    # Create Unity Environment
    log('Creating Unity virtual environment...'); print()
    env = VisualEnvironment(env_file, action_repeat)

    # Restore params from checkpoint if needed 
    if 'reloading' in agent.run_params:
        from_start = agent.run_params['from_start']

    if restore and not from_start:
        log('Restoring params...')
        it = agent.run_params['it']
        ep_start = agent.run_params['episodes']
        scores= agent.run_params['scores']
        avg_scores = agent.run_params['avg_scores']
        last_saved_score = agent.run_params['last_saved_score']
        q_metric = QMetric(agent.run_params['q_metric_states'], m)
        q_metrics = agent.run_params['q_metrics']
    else:
        avg_scores = []
        scores = []
        last_saved_score = 0
        it = 0 
        ep_start = 0
        q_metric = define_Q_metric(env, m, 100)
        q_metrics = []

    if 'reloading' in agent.run_params:
        restore = agent.run_params['restore']
            
    # Train agent
    log('Training'); print()
    with trange(ep_start, episodes) as t:

        for ep_i in t:
            score = 0
            agent.reset_episode()
            state = env.reset()
            for _ in range(steps):

                # Decay exploration epsilon (linear decay)
                eps = max(final_eps,ini_eps-(ini_eps-final_eps)/final_exp_it*it)
                
                # Step agent 
                action = agent.act(state, epsilon=eps)
                next_state, reward, done = env.step(action)
                agent.step(state, action, reward, next_state, done)
                score += reward
                state = next_state
                if done:
                    break
                it+=1 

            # Update metrics  
            q_metrics.append((ep_i+1, q_metric.evaluate()))
            scores.append((ep_i+1, score))
            t.set_postfix(it=it,epsilon=f'{eps:.3f}', q_eval= f'{q_metrics[-1][1]:.2f}', score=f'{score:.2f}')

            # Calculate score using policy epsilon=0.05 and 100 episodes
            if (ep_i+1) % log_every == 0:
                print()
                log('Evaluating current policy...')
                avg_score = evaluate_policy(env, agent)
                avg_scores.append((ep_i+1, avg_score))
                log(f'Average score: {avg_score:.2f}'); print()

                # Save agent if score is greater than threshold & last saved score
                if avg_score > save_thresh and avg_score > last_saved_score:
                    save(agent, out_file, 
                         ep_i+1, it, avg_scores, scores, q_metrics, last_saved_score
                    )

            # Reload the environment to fix memory leak issues 
            if (ep_i+1) % reload_every == 0:
                log('Reloading environment...')
                params = {
                    'episodes': ep_i+1,
                    'it': it,
                    'restore': restore,
                    'from_start': False, 
                    'reloading': True,
                    'avg_scores': avg_scores, 
                    'last_saved_score': last_saved_score,
                    'scores': scores,
                    'q_metric_states': q_metric.states.cpu().numpy(),
                    'q_metrics': q_metrics
                    }
                agent.save('reload.ckpt', run_params=params)
                env.close()
                reload_process()

    # Training done
    # Save if not already done
    if not os.path.isfile(out_file):
        save(agent, out_file, 
             episodes, it, avg_scores, scores, q_metrics, last_saved_score
        )

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description = 'Unity - Visual Banana Collector')  
    parser.add_argument("--env_file", help="Location of Unity env. file", default='data/Banana_x86_x64')
    parser.add_argument("--out_file", help="Checkpoint file", default='dbl_dqn_agent.ckpt')
    parser.add_argument("--restore", help="Restore checkpoint")
    parser.add_argument('--reload_every', help="Reload env. every number of episodes", default=1000)
    parser.add_argument("--log_every", help="Log metric every number of episodes", default=10)
    parser.add_argument("--episodes", help="Number of episodes to run", default=1000)
    parser.add_argument("--save_thresh", help="Saving threshold", default=10.0)
    parser.add_argument("--final_exp_it", help="final exploaration iteration", default=200000)
    args = parser.parse_args()

    train(
        env_file=args.env_file,
        out_file=args.out_file,
        restore=args.restore,
        reload_every=int(args.reload_every),
        log_every=int(args.log_every),
        episodes=int(args.episodes),
        save_thresh=float(args.save_thresh), 
        final_exp_it=int(args.final_exp_it)
    )

