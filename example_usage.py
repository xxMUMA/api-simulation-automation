"""
Example usage for simulation_api_wrapper.py

Before running:
1. Copy .env.example to .env
2. Fill in your private API base URL and credentials
3. Keep .env inside .gitignore
"""

from simulation_api_wrapper import SimulationAPIWrapper


def main():
    wrapper = SimulationAPIWrapper()

    # Replace this payload structure with your own API's required format.
    simulation_payload = {
        "type": "simulation_type_here",
        "settings": {
            "parameter_1": "value_1",
            "parameter_2": "value_2",
            "parameter_3": 123,
        },
        "strategy_expression": "your_private_expression_here",
    }

    result = wrapper.run_simulation(
        simulation_payload,
        save_local=True,
        classify_before_save=True,
    )

    print("Simulation result retrieved:")
    print(result)

    df = wrapper.create_dataframe_from_folder("approved_results")
    print(df.head())


if __name__ == "__main__":
    main()
