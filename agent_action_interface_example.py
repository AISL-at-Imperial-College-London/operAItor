"""Small example of the restricted operator/agent action interface."""
from integrated_plant import EpisodeRunner, IntegratedPlantDAE, SupervisoryAction, nominal_episode

plant = IntegratedPlantDAE()
env = EpisodeRunner(plant, nominal_episode(30.0))

print("Available authority:")
for key, value in env.action_catalog().items():
    print(f"\n{key}:\n{value}")

# Examples -- these modify persistent operator state.
env.apply_action(SupervisoryAction(throughput_target=80.0))
env.apply_action(SupervisoryAction(setpoints={"R_TIC": 334.0}))
env.apply_action(SupervisoryAction(modes={"TOP_TIC": "MANUAL"}, manual_outputs={"TOP_TIC": 50.0}))
env.apply_action(SupervisoryAction(auxiliary_commands={"H2_BDV": 0.20}))

# Process time advances only here.
obs, status = env.step(SupervisoryAction.no_action())
print("\nStatus:", status.status.value)
print("Action result:", obs["last_action_result"])
