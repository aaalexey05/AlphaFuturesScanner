#!/usr/bin/env python3
"""
AlphaFutures Scanner - Профессиональный бот для сканирования фьючерсов на Bybit
Автоматически находит торговые сигналы на основе комплексного чеклиста
"""

import asyncio
import logging
import logging.handlers
import json
import os
import time
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from dotenv import load_dotenv

import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from telegram import Bot
from telegram.error import TelegramError

# Загружаем переменные из .env файла
load_dotenv()

# Настройка расширенного логирования
def setup_logging():
    """Настройка расширенного логирования с ротацией"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    
    # Файловый обработчик с ротацией
    file_handler = logging.handlers.RotatingFileHandler(
        'trading_bot.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

@dataclass
class BotConfig:
    """Конфигурация бота с валидацией"""
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    TESTNET: bool = False # По умолчанию работаем на реальном рынке
    SCAN_INTERVAL: int = 300
    MAX_SYMBOLS: int = 50
    RISK_PER_TRADE: float = 1.0
    MIN_VOLUME_INCREASE: float = 1.2
    MAX_FUNDING_RATE: float = 0.0003
    MIN_LIQUIDITY: float = 20000
    RSI_MIN: int = 45
    RSI_MAX: int = 70
    ENABLE_HEALTH_CHECKS: bool = True
    HEALTH_CHECK_INTERVAL: int = 3600
    
    def __post_init__(self):
        """Валидация конфигурации после инициализации"""
        if not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_CHAT_ID:
            raise ValueError("TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID обязательны")
        
        if self.RISK_PER_TRADE > 5:
            logger.warning("Риск на сделку превышает 5% - это может быть опасно")

class ConnectionManager:
    """Менеджер подключений с повторными попытками"""
    
    def __init__(self, max_retries: int = 5, backoff_factor: float = 2.0):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.retry_count = 0
    
    async def execute_with_retry(self, func, *args, **kwargs):
        """Выполнение функции с повторными попытками при ошибках"""
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                self.retry_count = 0
                return result
            except Exception as e:
                last_exception = e
                self.retry_count += 1
                wait_time = self.backoff_factor ** attempt
                
                logger.warning(
                    f"Попытка {attempt+1}/{self.max_retries} не удалась для {func.__name__}. "
                    f"Ошибка: {str(e)}"
                )
                
                if attempt < self.max_retries - 1:
                    logger.info(f"Повторная попытка через {wait_time:.1f} секунд...")
                    await asyncio.sleep(wait_time)
        
        logger.error(f"Все {self.max_retries} попыток не удались для {func.__name__}")
        raise last_exception

class SignalStrength(Enum):
    """Сила торгового сигнала"""
    WEAK = 0.3
    MEDIUM = 0.6
    STRONG = 0.8
    VERY_STRONG = 0.9

class AlphaFuturesScanner:
    """Основной класс бота AlphaFutures Scanner"""
    
    def __init__(self, config: BotConfig):
        self.config = config
        self.connection_manager = ConnectionManager()
        self.telegram_bot = None
        self.bybit_client = None
        self.is_running = False
        self.last_scan_time = None
        self.start_time = datetime.now()
        self.errors_count = 0
        self.successful_scans = 0
        self.signals_sent = 0
        
        # Кэширование данных для оптимизации
        self.symbols_cache = {}
        self.cache_timeout = timedelta(minutes=5)

        # Путь к файлу истории торгов
        self.trade_history_file = "trade_history.json"
        self.trade_history = []
        
        # Создаем файл истории, если он не существует
        if not os.path.exists(self.trade_history_file):
            with open(self.trade_history_file, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=2, ensure_ascii=False)
            logger.info(f"Создан новый файл {self.trade_history_file}")
        
        self.setup_clients()
        self.load_trade_history()
        
        logger.info("AlphaFutures Scanner инициализирован")
    
    def setup_clients(self):
        """Инициализация клиентов с обработкой ошибок"""
        try:
            # Инициализация Telegram бота
            self.telegram_bot = Bot(token=self.config.TELEGRAM_BOT_TOKEN)
            
            # Инициализация Bybit клиента
            if self.config.BYBIT_API_KEY and self.config.BYBIT_API_SECRET:
                self.bybit_client = HTTP(
                    testnet=self.config.TESTNET,
                    api_key=self.config.BYBIT_API_KEY,
                    api_secret=self.config.BYBIT_API_SECRET,
                )
            else:
                self.bybit_client = HTTP(testnet=self.config.TESTNET)
            
            logger.info("Клиенты успешно инициализированы")
            
        except Exception as e:
            logger.error(f"Ошибка инициализации клиентов: {e}")
            raise
    
    def recover_trade_history(self):
        """Восстановление поврежденного файла trade_history.json"""
        try:
            backup_file = self.trade_history_file + '.bak'
            if os.path.exists(backup_file):
                with open(backup_file, 'r', encoding='utf-8') as f:
                    self.trade_history = json.load(f)
                logger.info(f"Восстановлено {len(self.trade_history)} записей из бэкапа")
                return

            # Попытка чтения файла построчно для нахождения последней валидной записи
            valid_data = []
            with open(self.trade_history_file, 'r', encoding='utf-8') as f:
                content = f.read()
                try:
                    valid_data = json.loads(content[:content.rfind('}')+1])
                    self.trade_history = valid_data if isinstance(valid_data, list) else [valid_data]
                    logger.info(f"Восстановлено {len(self.trade_history)} записей из поврежденного файла")
                except json.JSONDecodeError:
                    logger.error("Не удалось восстановить файл, создаем пустой")
                    self.trade_history = []
            
            # Создаем резервную копию поврежденного файла
            if os.path.exists(self.trade_history_file):
                os.rename(self.trade_history_file, self.trade_history_file + '.corrupted')
            with open(self.trade_history_file, 'w', encoding='utf-8') as f:
                json.dump(self.trade_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка восстановления истории торгов: {e}")
            self.trade_history = []
    
    def load_trade_history(self):
        """Загрузка истории торгов с обработкой ошибок"""
        self.trade_history = []
        if not os.path.exists(self.trade_history_file):
            logger.info("Файл истории торгов не найден, создан пустой список")
            return

        try:
            with open(self.trade_history_file, 'r', encoding='utf-8') as f:
                self.trade_history = json.load(f)
            logger.info(f"Загружено {len(self.trade_history)} записей из истории торгов")
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга trade_history.json: {e}")
            logger.info("Попытка восстановления файла...")
            self.recover_trade_history()
        except Exception as e:
            logger.error(f"Ошибка загрузки истории торгов: {e}")
    
    def save_trade_history(self):
        """Сохранение истории торгов с обработкой ошибок"""
        try:
            def convert_to_serializable(obj):
                if isinstance(obj, (np.bool_, bool)):
                    return bool(obj)
                if isinstance(obj, (np.floating, float)):
                    return float(obj)
                if isinstance(obj, (np.integer, int)):
                    return int(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                if isinstance(obj, Enum):
                    return obj.name
                if isinstance(obj, dict):
                    return {k: convert_to_serializable(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [convert_to_serializable(v) for v in obj]
                return obj
            
            serializable_history = convert_to_serializable(self.trade_history[-1000:])
            
            # Создаем резервную копию перед записью
            if os.path.exists(self.trade_history_file):
                os.rename(self.trade_history_file, self.trade_history_file + '.bak')
            
            with open(self.trade_history_file, 'w', encoding='utf-8') as f:
                json.dump(serializable_history, f, indent=2, ensure_ascii=False)
            logger.info(f"Сохранено {len(serializable_history)} записей в trade_history.json")
        except Exception as e:
            logger.error(f"Ошибка сохранения истории торгов: {e}")
    
    async def send_telegram_message(self, message: str, parse_mode: str = 'Markdown'):
        """Отправка сообщения в Telegram с повторными попытками"""
        try:
            await self.connection_manager.execute_with_retry(
                self.telegram_bot.send_message,
                chat_id=self.config.TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=parse_mode
            )
            logger.debug("Сообщение успешно отправлено в Telegram")
            
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение в Telegram: {e}")
            self.errors_count += 1
    
    async def get_account_balance(self) -> Optional[float]:
        """Получение баланса аккаунта"""
        if not self.config.BYBIT_API_KEY:
            return None
            
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.bybit_client.get_wallet_balance(accountType="UNIFIED")
            )
            balance = float(response['result']['list'][0]['totalEquity'])
            logger.info(f"Текущий баланс: {balance:.2f} USDT")
            return balance
        except Exception as e:
            logger.error(f"Ошибка получения баланса: {e}")
            return None
    
    async def calculate_position_size(self, symbol: str, entry_price: float, stop_loss: float) -> Dict[str, float]:
        """Расчет размера позиции на основе риск-менеджмента"""
        default_result = {
            'size': 0.0,
            'risk_amount': 0.0,
            'risk_percent': 0.0,
            'leverage_suggestion': 0.0
        }
        try:
            balance = await self.get_account_balance()
            if not balance or balance <= 0:
                logger.warning(f"Баланс 0 для {symbol}, позиция не рассчитана")
                return default_result
            
            risk_amount = balance * (self.config.RISK_PER_TRADE / 100)
            price_diff = abs(entry_price - stop_loss)
            risk_percent = (price_diff / entry_price) * 100 if entry_price != 0 else 0.0
            
            if risk_percent == 0:
                logger.warning(f"Нулевой риск для {symbol}, позиция не рассчитана")
                return default_result
            
            position_size = risk_amount / (price_diff / entry_price)
            max_position_size = balance * 0.1
            if position_size > max_position_size:
                position_size = max_position_size
                risk_amount = position_size * (price_diff / entry_price)
                logger.warning(f"Размер позиции ограничен до {max_position_size:.2f}")
            
            result = {
                'size': float(position_size),
                'risk_amount': float(risk_amount),
                'risk_percent': float(risk_percent),
                'leverage_suggestion': float(min(10, int(1 / (price_diff / entry_price))) if price_diff != 0 else 0)
            }
            
            logger.info(
                f"Расчет позиции для {symbol}: "
                f"размер = {position_size:.2f}, риск = {risk_amount:.2f} USDT, "
                f"риск % = {risk_percent:.1f}, плечо = {result['leverage_suggestion']}"
            )
            return result
        except Exception as e:
            logger.error(f"Ошибка расчета размера позиции для {symbol}: {e}")
            return default_result
    
    async def get_all_futures_symbols(self) -> List[str]:
        """Получение списка фьючерсных пар с кэшированием"""
        cache_key = "all_symbols"
        current_time = datetime.now()
        
        # Проверяем кэш
        if (cache_key in self.symbols_cache and 
            current_time - self.symbols_cache[cache_key]['timestamp'] < self.cache_timeout):
            return self.symbols_cache[cache_key]['data']
        
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.bybit_client.get_instruments_info(category="linear")
            )
            
            symbols = [item['symbol'] for item in response['result']['list']]
            
            # Сохраняем в кэш
            self.symbols_cache[cache_key] = {
                'data': symbols,
                'timestamp': current_time
            }
            
            logger.info(f"Получено {len(symbols)} фьючерсных пар")
            return symbols
            
        except Exception as e:
            logger.error(f"Ошибка получения списка пар: {e}")
            return []
    
    async def filter_symbols_by_liquidity(self, symbols: List[str]) -> List[str]:
        """Фильтрация символов по ликвидности и объему"""
        filtered_symbols = []
        
        for symbol in symbols[:self.config.MAX_SYMBOLS]:
            try:
                # Получаем данные об объеме
                ticker_response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.bybit_client.get_tickers(
                        category="linear",
                        symbol=symbol
                    )
                )
                
                ticker_data = ticker_response['result']['list'][0]
                volume_24h = float(ticker_data.get('volume24h', 0))
                turnover_24h = float(ticker_data.get('turnover24h', 0))
                
                # Фильтруем по минимальному объему и обороту
                if volume_24h > 100000 and turnover_24h > 1000000:
                    filtered_symbols.append(symbol)
                    
            except Exception as e:
                logger.warning(f"Не удалось получить данные для {symbol}: {e}")
                continue
        
        logger.info(f"После фильтрации осталось {len(filtered_symbols)} символов")
        return filtered_symbols
    
    async def get_symbol_data(self, symbol: str) -> Optional[Dict]:
        """Получение полных данных по символу"""
        try:
            # Получаем данные свечей
            kline_response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.bybit_client.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval="15",
                    limit=100
                )
            )
            
            # Получаем стакан цен
            orderbook_response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.bybit_client.get_orderbook(
                    category="linear",
                    symbol=symbol
                )
            )
            
            # Получаем ставку финансирования
            funding_response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.bybit_client.get_funding_rate_history(
                    category="linear",
                    symbol=symbol,
                    limit=1
                )
            )
            
            return {
                'symbol': symbol,
                'klines': kline_response['result']['list'],
                'orderbook': orderbook_response['result'],
                'funding_rate': funding_response['result']
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения данных для {symbol}: {e}")
            return None
    
    def calculate_technical_indicators(self, klines: List) -> Dict[str, float]:
        """Расчет технических индикаторов с обработкой ошибок"""
        try:
            if not klines or len(klines) < 50:
                return {}
            
            # Преобразуем данные в DataFrame
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ])
            
            # Конвертируем типы данных
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df = df.dropna()
            
            if len(df) < 20:
                return {}
            
            closes = df['close'].values
            
            # Вычисляем индикаторы
            indicators = {
                'current_price': closes[-1],
                'sma_20': np.mean(closes[-20:]),
                'sma_50': np.mean(closes[-50:]),
                'ema_20': pd.Series(closes).ewm(span=20).mean().iloc[-1],
                'rsi': self.calculate_rsi(closes),
                'macd': self.calculate_macd(closes)[0],
                'macd_signal': self.calculate_macd(closes)[1],
                'atr': self.calculate_atr(df),
                'volume_avg': np.mean(df['volume'].values[-20:])
            }
            
            return indicators
            
        except Exception as e:
            logger.error(f"Ошибка расчета индикаторов: {e}")
            return {}
    
    def calculate_rsi(self, prices: np.array, period: int = 14) -> float:
        """Расчет RSI"""
        try:
            deltas = np.diff(prices)
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = np.mean(gains[-period:])
            avg_loss = np.mean(losses[-period:])
            
            if avg_loss == 0:
                return 100
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return min(100, max(0, rsi))
            
        except Exception as e:
            logger.error(f"Ошибка расчета RSI: {e}")
            return 50
    
    def calculate_macd(self, prices: np.array, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
        """Расчет MACD"""
        try:
            exp1 = pd.Series(prices).ewm(span=fast).mean()
            exp2 = pd.Series(prices).ewm(span=slow).mean()
            macd_line = exp1 - exp2
            signal_line = macd_line.ewm(span=signal).mean()
            
            return macd_line.iloc[-1], signal_line.iloc[-1]
        except Exception as e:
            logger.error(f"Ошибка расчета MACD: {e}")
            return 0, 0
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Расчет Average True Range"""
        try:
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            atr = true_range.rolling(period).mean().iloc[-1]
            
            return atr
        except Exception as e:
            logger.error(f"Ошибка расчета ATR: {e}")
            return 0
    
    def run_enhanced_checklist(self, symbol: str, data: Dict, indicators: Dict) -> Tuple[bool, float, Dict]:
        """Улучшенный чеклист с весовой системой"""
        try:
            checks = {
                'Тренд': self.check_trend_alignment(indicators),
                'Объем': self.check_volume(data, indicators),
                'Моментум': self.check_momentum(indicators),
                'Ликвидность': self.check_liquidity(data),
                'Финансирование': self.check_funding_rate(data),
                'Волатильность': self.check_volatility(indicators),
                'Уровни': self.check_support_resistance(data, indicators),
                'Рыночные условия': self.check_market_conditions(symbol)
            }
            
            # Веса для каждого пункта
            weights = {
                'Тренд': 0.20,
                'Объем': 0.15,
                'Моментум': 0.15,
                'Ликвидность': 0.10,
                'Финансирование': 0.10,
                'Волатильность': 0.10,
                'Уровни': 0.10,
                'Рыночные условия': 0.10
            }
            
            # Расчет общего score
            total_score = 0
            for check_name, passed in checks.items():
                if passed:
                    total_score += weights.get(check_name, 0)
            
            # Определение силы сигнала
            signal_strength = self.determine_signal_strength(total_score)
            
            # Минимальный проходной балл - 70%
            passed = total_score >= 0.7
            
            logger.info(
                f"Чеклист для {symbol}: score = {total_score:.2f}, "
                f"пройдено = {passed}, сила = {signal_strength.name}"
            )
            
            return passed, total_score, checks
            
        except Exception as e:
            logger.error(f"Ошибка выполнения чеклиста для {symbol}: {e}")
            return False, 0, {}
    
    def determine_signal_strength(self, score: float) -> SignalStrength:
        """Определение силы сигнала на основе score"""
        if score >= SignalStrength.VERY_STRONG.value:
            return SignalStrength.VERY_STRONG
        elif score >= SignalStrength.STRONG.value:
            return SignalStrength.STRONG
        elif score >= SignalStrength.MEDIUM.value:
            return SignalStrength.MEDIUM
        else:
            return SignalStrength.WEAK
    
    def check_trend_alignment(self, indicators: Dict) -> bool:
        """Проверка соответствия тренду"""
        price = indicators.get('current_price', 0)
        sma_20 = indicators.get('sma_20', 0)
        ema_20 = indicators.get('ema_20', 0)
        
        return price > sma_20 and price > ema_20
    
    def check_volume(self, data: Dict, indicators: Dict) -> bool:
        """Проверка объема"""
        try:
            klines = data.get('klines', [])
            if not klines:
                return False
            
            # Получаем последние объемы
            volumes = [float(k[5]) for k in klines[-10:]]
            if not volumes:
                return False
            
            current_volume = volumes[-1]
            avg_volume = indicators.get('volume_avg', current_volume)
            
            return current_volume > avg_volume * self.config.MIN_VOLUME_INCREASE
            
        except Exception as e:
            logger.error(f"Ошибка проверки объема: {e}")
            return False
    
    def check_momentum(self, indicators: Dict) -> bool:
        """Проверка моментаума"""
        rsi = indicators.get('rsi', 50)
        macd = indicators.get('macd', 0)
        macd_signal = indicators.get('macd_signal', 0)
        
        rsi_ok = self.config.RSI_MIN <= rsi <= self.config.RSI_MAX
        macd_ok = macd > macd_signal
        
        return rsi_ok and macd_ok
    
    def check_liquidity(self, data: Dict) -> bool:
        """Проверка ликвидности"""
        try:
            orderbook = data.get('orderbook', {})
            bids = orderbook.get('b', [])
            asks = orderbook.get('a', [])
            
            if not bids or not asks:
                return False
            
            # Суммируем ликвидность в стакане
            bid_liquidity = sum(float(bid[1]) * float(bid[0]) for bid in bids[:5])
            ask_liquidity = sum(float(ask[1]) * float(ask[0]) for ask in asks[:5])
            
            return bid_liquidity > self.config.MIN_LIQUIDITY and ask_liquidity > self.config.MIN_LIQUIDITY
            
        except Exception as e:
            logger.error(f"Ошибка проверки ликвидности: {e}")
            return False
    
    def check_funding_rate(self, data: Dict) -> bool:
        """Проверка ставки финансирования"""
        try:
            funding_data = data.get('funding_rate', {})
            if not funding_data or 'list' not in funding_data:
                return False
            
            funding_list = funding_data['list']
            if not funding_list:
                return False
            
            rate = float(funding_list[0].get('fundingRate', 0))
            return abs(rate) < self.config.MAX_FUNDING_RATE
            
        except Exception as e:
            logger.error(f"Ошибка проверки ставки финансирования: {e}")
            return False
    
    def check_volatility(self, indicators: Dict) -> bool:
        """Проверка волатильности"""
        atr = indicators.get('atr', 0)
        current_price = indicators.get('current_price', 1)
        
        if current_price == 0:
            return False
        
        atr_percent = (atr / current_price) * 100
        return atr_percent < 5.0
    
    def check_support_resistance(self, data: Dict, indicators: Dict) -> bool:
        """Проверка поддержки и сопротивления"""
        try:
            klines = data.get('klines', [])
            if not klines:
                return False
            
            # Получаем цены закрытия
            closes = [float(k[4]) for k in klines[-30:]]
            current_price = indicators.get('current_price', 0)
            
            if not closes:
                return False
            
            # Находим ключевые уровни
            resistance = max(closes[-20:])
            support = min(closes[-20:])
            
            # Проверяем, что цена не у ключевого сопротивления
            distance_to_resistance = (resistance - current_price) / current_price
            return distance_to_resistance > 0.01
            
        except Exception as e:
            logger.error(f"Ошибка проверки уровней: {e}")
            return False
    
    def check_market_conditions(self, symbol: str) -> bool:
        """Проверка общих рыночных условий"""
        return True
    
    async def analyze_symbol(self, symbol: str):
        """Анализ конкретного символа"""
        try:
            logger.debug(f"Анализируем символ: {symbol}")
            
            # Получаем данные
            data = await self.get_symbol_data(symbol)
            if not data:
                return
            
            # Вычисляем индикаторы
            indicators = self.calculate_technical_indicators(data['klines'])
            if not indicators:
                return
            
            # Запускаем чеклист
            checklist_passed, score, checklist_results = self.run_enhanced_checklist(
                symbol, data, indicators
            )
            
            if checklist_passed:
                # Определяем точку входа и стоп-лосс
                entry_price = indicators['current_price']
                stop_loss = entry_price * 0.98  # Стоп-лосс на 2% ниже
                
                # Рассчитываем размер позиции
                position_info = await self.calculate_position_size(
                    symbol, entry_price, stop_loss
                )
                
                # Формируем сигнал
                signal = {
                    'symbol': symbol,
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': entry_price * 1.06,  # Тейк-профит на 6% выше
                    'score': score,
                    'strength': self.determine_signal_strength(score),
                    'position_size': position_info['size'],
                    'risk_amount': position_info['risk_amount'],
                    'timestamp': datetime.now().isoformat(),
                    'indicators': indicators
                }
                
                # Отправляем сигнал
                await self.send_trading_signal(signal, checklist_results, position_info)
                
                # Сохраняем в историю
                self.trade_history.append({
                    'signal': signal,
                    'checklist_results': checklist_results,
                    'timestamp': datetime.now().isoformat()
                })
                self.save_trade_history()
                
                self.signals_sent += 1
                
        except Exception as e:
            logger.error(f"Ошибка анализа {symbol}: {e}")
            self.errors_count += 1
    
    async def send_trading_signal(self, signal: Dict, checklist_results: Dict, position_info: Dict):
        """Отправка детализированного торгового сигнала"""
        try:
            strength_emoji = {
                SignalStrength.WEAK: "🟡",
                SignalStrength.MEDIUM: "🟢",
                SignalStrength.STRONG: "🔵",
                SignalStrength.VERY_STRONG: "🚀"
            }
            emoji = strength_emoji.get(signal['strength'], "📈")
            
            # Безопасное получение параметров
            risk_percent = position_info.get('risk_percent', 0.0)
            leverage_suggestion = position_info.get('leverage_suggestion', 0.0)
            
            message_parts = [
                f"{emoji} *ТОРГОВЫЙ СИГНАЛ* {emoji}",
                f"*Токен:* `{signal['symbol']}`",
                f"*Сила сигнала:* {signal['strength'].name.replace('_', ' ').title()} ({signal['score']:.1%})",
                "",
                "*🎯 Параметры входа:*",
                f"• Цена входа: `${signal['entry_price']:.4f}`",
                f"• Стоп-лосс: `${signal['stop_loss']:.4f}` (-{100*(1-signal['stop_loss']/signal['entry_price']):.1f}%)",
                f"• Тейк-профит: `${signal['take_profit']:.4f}` (+{100*(signal['take_profit']/signal['entry_price']-1):.1f}%)",
                f"• Риск/прибыль: 1:{((signal['take_profit']-signal['entry_price'])/(signal['entry_price']-signal['stop_loss'])):.1f}",
                "",
                "*📊 Параметры позиции:*",
                f"• Размер позиции: `{signal['position_size']:.2f} USDT`",
                f"• Сумма риска: `{signal['risk_amount']:.2f} USDT`",
                f"• Риск на сделку: `{risk_percent:.1f}%`",
                f"• Рекомендуемое плечо: `{leverage_suggestion:.0f}x`",
                "",
                "*✅ Результаты чеклиста:*"
            ]
            
            for check_name, passed in checklist_results.items():
                status = "✅" if passed else "❌"
                message_parts.append(f"{status} {check_name}")
            
            message_parts.extend([
                "",
                f"*📈 Индикаторы:* RSI {signal['indicators'].get('rsi', 0):.1f}, "
                f"MACD {signal['indicators'].get('macd', 0):.4f}",
                f"*⏰ Время:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                ""
            ])
            
            message = "\n".join(message_parts)
            await self.send_telegram_message(message)
            logger.info(f"Торговый сигнал отправлен для {signal['symbol']}")
        except Exception as e:
            logger.error(f"Ошибка отправки сигнала для {signal['symbol']}: {e}")
            self.errors_count += 1
    
    async def send_health_report(self):
        """Отправка отчета о состоянии бота"""
        try:
            uptime = datetime.now() - self.start_time
            hours, remainder = divmod(uptime.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            
            message = "\n".join([
                "🤖 *ОТЧЕТ О СОСТОЯНИИ БОТА*",
                "",
                f"*Время работы:* {int(hours)}ч {int(minutes)}м {int(seconds)}с",
                f"*Последнее сканирование:* {self.last_scan_time.strftime('%H:%M:%S') if self.last_scan_time else 'Никогда'}",
                f"*Успешных сканирований:* {self.successful_scans}",
                f"*Отправлено сигналов:* {self.signals_sent}",
                f"*Ошибок:* {self.errors_count}",
                f"*Статус:* {'🟢 РАБОТАЕТ' if self.is_running else '🔴 ОСТАНОВЛЕН'}",
                "",
                f"*Следующее сканирование:* через {self.config.SCAN_INTERVAL} сек",
                f"*Мониторинг токенов:* {self.config.MAX_SYMBOLS}",
                f"*Риск на сделку:* {self.config.RISK_PER_TRADE}%",
            ])
            
            await self.send_telegram_message(message)
            logger.info("Отправлен отчет о состоянии бота")
            
        except Exception as e:
            logger.error(f"Ошибка отправки отчета: {e}")
    
    async def scan_market(self):
        """Сканирование рынка на наличие сигналов"""
        try:
            logger.info("Начинаем сканирование рынка...")
            start_time = time.time()
            
            # Получаем и фильтруем символы
            all_symbols = await self.get_all_futures_symbols()
            filtered_symbols = await self.filter_symbols_by_liquidity(all_symbols)
            
            if not filtered_symbols:
                logger.warning("Нет подходящих символов для анализа")
                return
            
            logger.info(f"Анализируем {len(filtered_symbols)} символов...")
            
            # Анализируем каждый символ
            tasks = []
            for symbol in filtered_symbols:
                task = asyncio.create_task(self.analyze_symbol(symbol))
                tasks.append(task)
                
                # Ограничиваем количество одновременных запросов
                if len(tasks) >= 5:
                    await asyncio.gather(*tasks)
                    tasks = []
                    await asyncio.sleep(0.1)
            
            # Обрабатываем оставшиеся задачи
            if tasks:
                await asyncio.gather(*tasks)
            
            self.last_scan_time = datetime.now()
            self.successful_scans += 1
            
            scan_duration = time.time() - start_time
            logger.info(f"Сканирование завершено за {scan_duration:.2f} секунд")
            
        except Exception as e:
            logger.error(f"Ошибка сканирования рынка: {e}")
            self.errors_count += 1
    
    async def start(self):
        """Запуск бота"""
        if self.is_running:
            logger.warning("Бот уже запущен")
            return
        
        self.is_running = True
        await self.send_telegram_message(
            "🚀 *AlphaFutures Scanner запущен!* \n"
            "Начинаю мониторинг фьючерсов на Bybit..."
        )
        
        logger.info("AlphaFutures Scanner запущен и начал работу")
        
        try:
            health_check_task = None
            
            # Запускаем периодическую проверку здоровья
            if self.config.ENABLE_HEALTH_CHECKS:
                health_check_task = asyncio.create_task(self.health_check_loop())
            
            # Основной цикл работы
            while self.is_running:
                try:
                    await self.scan_market()
                    
                    # Ожидание следующего сканирования
                    wait_time = self.config.SCAN_INTERVAL
                    logger.info(f"Следующее сканирование через {wait_time} секунд")
                    
                    for i in range(wait_time):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                        
                except Exception as e:
                    logger.error(f"Ошибка в основном цикле: {e}")
                    self.errors_count += 1
                    await asyncio.sleep(60)
                    
        except Exception as e:
            logger.error(f"Критическая ошибка в работе бота: {e}")
            await self.send_telegram_message(f"❌ *Критическая ошибка:* {str(e)}")
        finally:
            self.is_running = False
            if health_check_task:
                health_check_task.cancel()
            
            await self.send_telegram_message("🛑 *AlphaFutures Scanner остановлен*")
            logger.info("AlphaFutures Scanner завершил работу")
    
    async def health_check_loop(self):
        """Цикл проверки здоровья бота"""
        while self.is_running:
            try:
                await asyncio.sleep(self.config.HEALTH_CHECK_INTERVAL)
                
                # Проверяем, когда было последнее сканирование
                if (self.last_scan_time and 
                    (datetime.now() - self.last_scan_time).total_seconds() > self.config.SCAN_INTERVAL * 2):
                    await self.send_telegram_message(
                        "⚠️ *ВНИМАНИЕ:* Бот не выполнял сканирование дольше ожидаемого времени"
                    )
                
                # Отправляем отчет каждые 6 часов
                if self.config.HEALTH_CHECK_INTERVAL >= 21600:
                    await self.send_health_report()
                    
            except Exception as e:
                logger.error(f"Ошибка в health check loop: {e}")
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Получена команда остановки бота...")
        self.is_running = False

def setup_signal_handlers(bot: AlphaFuturesScanner):
    """Настройка обработчиков сигналов для graceful shutdown"""
    def signal_handler(signum, frame):
        logger.info(f"Получен сигнал {signum}, инициируем остановку...")
        asyncio.create_task(bot.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

async def main():
    """Основная функция запуска бота"""
    try:
        # Загрузка конфигурации из переменных окружения
        config = BotConfig(
            TELEGRAM_BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN', ''),
            TELEGRAM_CHAT_ID=os.getenv('TELEGRAM_CHAT_ID', ''),
            BYBIT_API_KEY=os.getenv('BYBIT_API_KEY', ''),
            BYBIT_API_SECRET=os.getenv('BYBIT_API_SECRET', ''),
            TESTNET=os.getenv('TESTNET', 'True').lower() == 'true',
            SCAN_INTERVAL=int(os.getenv('SCAN_INTERVAL', '300')),
            MAX_SYMBOLS=int(os.getenv('MAX_SYMBOLS', '50')),
            RISK_PER_TRADE=float(os.getenv('RISK_PER_TRADE', '1.0')),
            ENABLE_HEALTH_CHECKS=os.getenv('ENABLE_HEALTH_CHECKS', 'True').lower() == 'true',
            HEALTH_CHECK_INTERVAL=int(os.getenv('HEALTH_CHECK_INTERVAL', '3600'))
        )
        
        # Создание и запуск бота
        bot = AlphaFuturesScanner(config)
        setup_signal_handlers(bot)
        
        # Запуск бота
        await bot.start()
        
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (Ctrl+C)")
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Проверка обязательных переменных окружения
    if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'):
        print("❌ Ошибка: Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
        print("\n📝 Инструкция по настройке:")
        print("1. Создайте файл .env в корне проекта")
        print("2. Заполните его по примеру .env.example")
        print("3. Получите TELEGRAM_BOT_TOKEN у @BotFather")
        print("4. Получите TELEGRAM_CHAT_ID у @userinfobot")
        print("\n⚡ Быстрый старт:")
        print("   pip install -r requirements.txt")
        print("   cp .env.example .env")
        print("   # отредактируйте .env файл")
        print("   python alpha_futures_scanner.py")
        sys.exit(1)
    
    # Запуск бота
    asyncio.run(main())
