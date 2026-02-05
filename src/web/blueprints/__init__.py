"""Flask blueprints for the EPUB translator web interface."""

from .export import export_bp
from .glossary import glossary_bp
from .home import home_bp
from .projects import projects_bp
from .review import review_bp
from .translation import translation_bp

__all__ = ['export_bp', 'glossary_bp', 'home_bp', 'projects_bp', 'review_bp', 'translation_bp']
