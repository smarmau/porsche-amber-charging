# Porsche Amber Charging Controller

A smart charging controller for Porsche electric vehicles that optimizes charging based on real-time electricity prices from Amber Energy.

## Features

- Authentication with Porsche Connect API with automatic CAPTCHA solving
- Integration with Amber Energy API for real-time electricity pricing
- Price-based smart charging control (charge when electricity is cheap)
- Configurable price thresholds for charging decisions
- Automatic detection of vehicle plug-in status
- Robust error handling and network resilience
- Web interface for monitoring and manual control

## Prerequisites

- Python 3.8+ (recommended: Python 3.12)
- Porsche Connect account credentials
- Amber Energy API key (for real-time pricing)
- 2captcha API key (optional, for automatic CAPTCHA solving)

## Installation

1. Clone this repository:

```bash
git clone https://github.com/smarmau/porsche-amber-charging.git
cd porsche-amber-charging
```

2. Create a virtual environment and activate it:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install the required dependencies:

```bash
pip install -r requirements.txt
```

4. Create a `.env` file with your credentials:

```env
PORSCHE_EMAIL=your.email@example.com
PORSCHE_PASSWORD=your_password
AMBER_API_KEY=your_amber_api_key
2CAPTCHA_API_KEY=your_2captcha_api_key  # Optional
```

## Usage

1. Run the application using the provided run script (which uses Uvicorn):

```bash
python run.py
```

Alternatively, you can run it directly with Uvicorn:

```bash
uvicorn porsche_charging_app.main:app --host 0.0.0.0 --port 8000 --reload
```

2. Open your browser and navigate to:

```text
http://localhost:8000
```

3. The application will automatically attempt to authenticate with Porsche Connect and display your vehicle information.

4. The smart charging controller will run in the background, automatically starting and stopping charging based on the current electricity prices from Amber Energy.

## Project Structure

```
porsche-amber/
├── porsche_charging_app/       # Main application package
│   ├── api/                    # API routes
│   ├── core/                   # Core configuration and utilities
│   ├── models/                 # Database models
│   ├── services/               # Service layer for external APIs
│   │   ├── charge_controller.py # Smart charging logic
│   │   ├── porsche_service.py  # Porsche Connect API integration
│   │   └── price_service.py    # Amber Energy API integration
│   ├── static/                 # Static assets
│   ├── templates/              # HTML templates
│   ├── utils/                  # Utility functions
│   ├── config.json             # Application configuration
│   └── main.py                 # FastAPI application
├── .env                        # Environment variables (not in git)
├── .gitignore                  # Git ignore file
├── requirements.txt            # Python dependencies
└── run.py                      # Entry point script
```

## License

This project is for personal use only and is not affiliated with Porsche AG.

## Acknowledgements

- [pyporscheconnectapi](https://github.com/CJNE/pyporscheconnectapi) - Python library for Porsche Connect API
- [Amber Energy API](https://amber.com.au/developers) - Real-time electricity pricing API
- [FastAPI](https://fastapi.tiangolo.com/) - Modern web framework for building APIs
- [2captcha](https://2captcha.com/) - CAPTCHA solving service
