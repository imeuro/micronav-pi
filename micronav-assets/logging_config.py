#!/usr/bin/env python3
"""
Configurazione logging per MicroNav Raspberry Pi
Gestisce la configurazione centralizzata del logging con livelli diversi per console e file
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from config import get_logging_config

def setup_logging():
    """Configura il logging utilizzando la configurazione centralizzata"""
    log_config = get_logging_config()
    
    # Converte il livello da stringa a costante logging
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    level = level_map.get(log_config['level'], logging.INFO)
    
    # Crea la directory dei log se non esiste
    log_dir = os.path.dirname(log_config['file'])
    try:
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
    except (OSError, PermissionError) as e:
        # Se non possiamo creare la directory (es. su macOS), usa un file temporaneo
        print(f"⚠️ Impossibile creare directory log {log_dir}: {e}")
        log_config['file'] = '/tmp/micronav.log'  # Fallback per test
    
    # File handler con rotazione - Livello DEBUG per file
    file_handler = RotatingFileHandler(
        log_config['file'],
        maxBytes=log_config['max_size'],
        backupCount=log_config['backup_count']
    )
    file_handler.setFormatter(logging.Formatter(log_config['format']))
    file_handler.setLevel(logging.DEBUG)  # File: DEBUG (tutto)
    
    # Console handler - Livello INFO per console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_config['format']))
    console_handler.setLevel(logging.INFO)  # Console: INFO e superiori
    
    # Configura il logger root al livello più basso (DEBUG)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Logger root: DEBUG (per permettere tutto)
    
    # Rimuovi handler esistenti per evitare duplicati
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Aggiungi i nostri handler
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    return root_logger

def get_logger(name: str = None):
    """Restituisce un logger configurato"""
    if name is None:
        name = __name__
    return logging.getLogger(name)

# Inizializza automaticamente il logging quando il modulo viene importato
setup_logging()
