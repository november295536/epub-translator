"""Flask web server for translation review."""

from __future__ import annotations

import os
from flask import Flask

from src.web.blueprints import review_bp


def create_app(project_dir: str) -> Flask:
    """Create and configure the Flask app for reviewing translations."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
        static_folder=os.path.join(os.path.dirname(__file__), 'static'),
    )
    app.config['PROJECT_DIR'] = project_dir

    # Register blueprints
    app.register_blueprint(review_bp)

    return app
