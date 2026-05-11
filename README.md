# Simulation API Wrapper

A platform-neutral Python wrapper for automating simulation workflows.

## Features

- Submit simulation jobs through an API
- Track simulation progress using polling
- Retrieve summary results and validation checks
- Save results as JSON files
- Convert saved results into a pandas DataFrame
- Classify results using configurable rules
- Plot quick result summaries

## Repository Safety

This repository does **not** include:

- Private API URLs
- Real usernames or passwords
- Private platform names
- Confidential simulation expressions
- Real result JSON files

## Project Structure

```text
simulation-api-wrapper/
├── simulation_api_wrapper.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── examples/
    └── example_usage.py
```

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file based on `.env.example`:

```env
SIM_API_BASE_URL=https://your-api-base-url.com
SIM_API_USERNAME=your_username_here
SIM_API_PASSWORD=your_password_here
```

Do not upload your `.env` file to GitHub.

## Usage

```python
from simulation_api_wrapper import SimulationAPIWrapper

wrapper = SimulationAPIWrapper()

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

df = wrapper.create_dataframe_from_folder("approved_results")
print(df.head())
```

## Custom Classification Rules

```python
stage1_rules = {
    "score": {"min": 1.2},
    "stability": {"min": 1.0},
    "turnover": {"min": 0.10},
}

stage2_rules = {
    "self_correlation": {"max": 0.65},
    "production_correlation": {"max": 0.65},
}

result = wrapper.run_simulation(
    simulation_payload,
    classify_before_save=True,
    stage1_rules=stage1_rules,
    stage2_rules=stage2_rules,
)
```

## Notes

This project is designed as a reusable API automation template. Replace the environment variables, endpoint paths, payload structure, and result-field aliases based on your own private API requirements.
