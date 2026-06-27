import cProfile
import pstats
import argparse

def main(profiles: int, device: str):
    # Placeholder for the actual training function call
    # Replace this with the actual training function you want to profile
    from optimizer.global_planner import plan_global_inventory  # Example import

    # Simulate running the training process multiple times
    for _ in range(profiles):
        # Example input data (replace with actual input data)
        player_state = {}  # Replace with actual state
        knowledge = {}     # Replace with actual knowledge

        # Profile the function call
        profiler = cProfile.Profile()
        profiler.enable()

        plan_global_inventory(player_state, knowledge)

        profiler.disable()
        stats = pstats.Stats(profiler).sort_stats('cumulative')
        stats.print_stats(10)  # Print top 10 functions

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Profile CPU bottlenecks causing GPU idle.")
    parser.add_argument("--profiles", type=int, required=True, help="Number of profiles to run")
    parser.add_argument("--device", type=str, required=True, help="Device to use (e.g., cuda)")
    args = parser.parse_args()

    main(args.profiles, args.device)
