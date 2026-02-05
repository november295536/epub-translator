"""Flask web server for translation review."""

from __future__ import annotations

import logging
import os
from flask import Flask

from src.web.blueprints import export_bp, glossary_bp, home_bp, projects_bp, review_bp, translation_bp


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

    # Configure logging for all src.* modules
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    # Register blueprints
    app.register_blueprint(export_bp)
    app.register_blueprint(glossary_bp)
    app.register_blueprint(home_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(translation_bp)

    return app
