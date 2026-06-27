"""Strategy base class"""
from abc import ABC, abstractmethod
import pandas as pd
class BaseStrategy(ABC):
    def __init__(self, name, config):
        self.name = name; self.config = config
    @abstractmethod
    def generate_signals(self, data: pd.DataFrame):
        pass
