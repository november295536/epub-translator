"""Flask web server for translation review."""

from __future__ import annotations

import os
from flask import Flask

from src.web.blueprints import home_bp, projects_bp, review_bp


def create_app(projects_dir: str | None = None) -> Flask:
    """Create and configure the Flask app.

    Args:
        projects_dir: Directory containing project folders.
                      Defaults to 'projects/' in the repository root.

    Returns:
        Configured Flask application.
    """
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
        static_folder=os.path.join(os.path.dirname(__file__), 'static'),
    )

    # Default to 'projects/' directory
    if projects_dir is None:
        projects_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'projects'
        )

    app.config['PROJECTS_DIR'] = projects_dir

    # Register blueprints
    app.register_blueprint(home_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(review_bp)

    return app
