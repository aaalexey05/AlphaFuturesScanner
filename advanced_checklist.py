#!/usr/bin/env python3
"""
Расширенные пункты чеклиста для AlphaFutures Scanner
Дополнительные проверки для повышения качества торговых сигналов
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any, Tuple
import logging
import asyncio
from pybit.unified_trading import HTTP  # Предполагаем, что у нас есть асинхронный клиент, если нет - адаптируем ниже

logger = logging.getLogger(__name__)

class AdvancedChecklist:
    """Класс с расширенными проверками для чеклиста"""
    
    def __init__(self, bybit_client: HTTP):
        self.bybit_client = bybit_client
    
    # 🔍 Технический анализ (расширенный)
    
    async def check_multi_timeframe_alignment(self, symbol: str) -> Tuple[bool, Dict]:
        """Проверка согласованности сигналов на разных таймфреймах"""
        try:
            timeframes = [
                ('5', '5min'),
                ('15', '15min'), 
                ('60', '1h'),
                ('240', '4h')
            ]
            
            results = {}
            bullish_count = 0
            total_timeframes = len(timeframes)
            
            tasks = []
            for tf_code, tf_name in timeframes:
                task = asyncio.create_task(self._check_single_timeframe(symbol, tf_code, tf_name))
                tasks.append((task, tf_name))
            
            for task, tf_name in tasks:
                is_bullish = await task
                results[tf_name] = is_bullish
                if is_bullish:
                    bullish_count += 1
            
            # Как минимум 75% таймфреймов должны быть бычьими
            passed = (bullish_count / total_timeframes) >= 0.75
            
            return passed, {
                'bullish_count': bullish_count,
                'total_timeframes': total_timeframes,
                'timeframe_results': results
            }
            
        except Exception as e:
            logger.error(f"Ошибка проверки множественных ТФ: {e}")
            return False, {'error': str(e)}
    
    async def _check_single_timeframe(self, symbol: str, tf_code: str, tf_name: str) -> bool:
        """Вспомогательный метод для проверки одного таймфрейма"""
        try:
            kline_response = self.bybit_client.get_kline(
                category="linear",
                symbol=symbol,
                interval=tf_code,
                limit=50
            )
            klines = kline_response['result']['list']
            if not klines:
                return False
            return self._is_timeframe_bullish(klines)
        except Exception as e:
            logger.warning(f"Ошибка анализа ТФ {tf_name} для {symbol}: {e}")
            return False
    
    def _is_timeframe_bullish(self, klines: List) -> bool:
        """Определение бычьего тренда на таймфрейме"""
        try:
            if len(klines) < 20:
                return False
            
            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            
            # Проверяем восходящую структуру (higher highs, higher lows)
            recent_highs = highs[-10:]
            recent_lows = lows[-10:]
            
            # Текущая цена выше средних значений
            current_price = closes[-1]
            sma_20 = np.mean(closes[-20:])
            sma_50 = np.mean(closes[-50:]) if len(closes) >= 50 else sma_20
            
            # Объем выше среднего
            volumes = [float(k[5]) for k in klines[-10:]]
            avg_volume = np.mean(volumes)
            current_volume = volumes[-1] if volumes else 0
            
            return (current_price > sma_20 > sma_50 and 
                    current_volume > avg_volume * 1.1)
                    
        except Exception as e:
            logger.error(f"Ошибка определения тренда ТФ: {e}")
            return False
    
    async def check_rsi_divergence(self, klines: List) -> Tuple[bool, Dict]:
        """Проверка бычьих дивергенций RSI"""
        try:
            if len(klines) < 30:
                return False, {'error': 'Недостаточно данных'}
            closes = [float(k[4]) for k in klines]
            lows = [float(k[3]) for k in klines]
            
            # Рассчитываем RSI для последних 30 свечей
            rsi_values = []
            for i in range(14, len(closes)):
                period_closes = closes[i-14:i+1]
                rsi = self._calculate_rsi(period_closes)
                rsi_values.append(rsi)
            
            if len(rsi_values) < 16:
                return False, {'error': 'Недостаточно данных RSI'}
            
            # Ищем дивергенции в последних 15 периодах
            price_lows = lows[-16:]  # Низы цен
            rsi_lows = rsi_values[-16:]  # Низы RSI
            
            # Ищем бычью дивергенцию
            bullish_divergence = self._find_bullish_divergence(price_lows, rsi_lows)
            
            return bullish_divergence, {
                'current_rsi': rsi_values[-1],
                'divergence_found': bullish_divergence,
                'price_lows': price_lows[-5:],
                'rsi_lows': rsi_lows[-5:]
            }
            
        except Exception as e:
            logger.error(f"Ошибка проверки дивергенции RSI: {e}")
            return False, {'error': str(e)}
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Расчет RSI"""
        if len(prices) < period + 1:
            return 50
        
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
    
    def _find_bullish_divergence(self, price_lows: List[float], rsi_lows: List[float]) -> bool:
        """Поиск бычьей дивергенции"""
        if len(price_lows) < 6 or len(rsi_lows) < 6:
            return False
        
        # Берем последние 3 минимума
        recent_price_lows = price_lows[-6:]
        recent_rsi_lows = rsi_lows[-6:]
        
        # Находим локальные минимумы
        price_minima = self._find_local_minima(recent_price_lows)
        rsi_minima = self._find_local_minima(recent_rsi_lows)
        
        if len(price_minima) < 2 or len(rsi_minima) < 2:
            return False
        
        # Проверяем дивергенцию: цена делает более низкие минимумы, RSI - более высокие
        latest_price_min = min(price_minima[-2:])
        latest_rsi_min = min(rsi_minima[-2:])
        previous_price_min = max(price_minima[-2:])
        previous_rsi_min = max(rsi_minima[-2:])
        
        return (latest_price_min < previous_price_min and 
                latest_rsi_min > previous_rsi_min)
    
    def _find_local_minima(self, data: List[float], window: int = 3) -> List[float]:
        """Поиск локальных минимумов"""
        minima = []
        for i in range(window, len(data) - window):
            if (data[i] == min(data[i-window:i+window+1]) and 
                data[i] != data[i-1] and data[i] != data[i+1]):
                minima.append(data[i])
        return minima
    
    async def check_volume_clusters(self, orderbook: Dict, current_price: float) -> Tuple[bool, Dict]:
        """Анализ кластеров объема на ключевых уровнях"""
        try:
            bids = orderbook.get('b', [])
            asks = orderbook.get('a', [])
            
            if not bids or not asks:
                return False, {'error': 'Нет данных стакана'}
            
            # Анализируем объемы вблизи текущей цены
            price_range = 0.02  # ±2% от текущей цены
            lower_bound = current_price * (1 - price_range)
            upper_bound = current_price * (1 + price_range)
            
            bid_volume_near = 0
            ask_volume_near = 0
            
            # Суммируем объемы бидов рядом с ценой
            for bid in bids:
                price = float(bid[0])
                volume = float(bid[1])
                if lower_bound <= price <= current_price:
                    bid_volume_near += volume * price
            
            # Суммируем объемы асков рядом с ценой
            for ask in asks:
                price = float(ask[0])
                volume = float(ask[1])
                if current_price <= price <= upper_bound:
                    ask_volume_near += volume * price
            
            # Рассчитываем баланс объемов
            total_volume_near = bid_volume_near + ask_volume_near
            if total_volume_near == 0:
                return False, {'error': 'Нет объемов рядом'}
            
            bid_ratio = bid_volume_near / total_volume_near
            ask_ratio = ask_volume_near / total_volume_near
            
            # Преимущество покупателей (бидов)
            passed = bid_ratio > ask_ratio
            
            return passed, {
                'bid_volume_usdt': bid_volume_near,
                'ask_volume_usdt': ask_volume_near,
                'bid_ratio': bid_ratio,
                'ask_ratio': ask_ratio,
                'volume_imbalance': bid_ratio - ask_ratio
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа кластеров объема: {e}")
            return False, {'error': str(e)}
    
    # 📈 Продвинутые индикаторы
    
    async def check_multi_timeframe_rsi(self, symbol: str) -> Tuple[bool, Dict]:
        """Проверка RSI на нескольких таймфреймах"""
        try:
            timeframes = [
                ('15', 45, 70),  # 15min, RSI между 45-70
                ('60', 40, 75),  # 1h, RSI между 40-75  
                ('240', 35, 80)  # 4h, RSI между 35-80
            ]
            
            results = {}
            passed_timeframes = 0
            
            tasks = []
            for tf_code, min_rsi, max_rsi in timeframes:
                task = asyncio.create_task(self._check_single_rsi(symbol, tf_code, min_rsi, max_rsi))
                tasks.append((task, tf_code))
            
            for task, tf_code in tasks:
                passed, rsi, details = await task
                results[tf_code] = details
                if passed:
                    passed_timeframes += 1
            
            # Как минимум 2 из 3 ТФ должны соответствовать
            passed = passed_timeframes >= 2
            
            return passed, {
                'passed_timeframes': passed_timeframes,
                'total_timeframes': len(timeframes),
                'timeframe_results': results
            }
            
        except Exception as e:
            logger.error(f"Ошибка проверки RSI на нескольких ТФ: {e}")
            return False, {'error': str(e)}
    
    async def _check_single_rsi(self, symbol: str, tf_code: str, min_rsi: float, max_rsi: float) -> Tuple[bool, float, Dict]:
        """Вспомогательный метод для проверки RSI одного таймфрейма"""
        try:
            kline_response = self.bybit_client.get_kline(
                category="linear",
                symbol=symbol,
                interval=tf_code,
                limit=100
            )
            klines = kline_response['result']['list']
            if not klines:
                return False, 50, {'passed': False, 'rsi': 50, 'error': 'Нет данных'}
            
            closes = [float(k[4]) for k in klines]
            rsi = self._calculate_rsi(closes)
            passed = min_rsi <= rsi <= max_rsi
            return passed, rsi, {
                'passed': passed,
                'rsi': rsi,
                'min_rsi': min_rsi,
                'max_rsi': max_rsi
            }
        except Exception as e:
            logger.warning(f"Ошибка RSI на ТФ {tf_code}: {e}")
            return False, 50, {'passed': False, 'rsi': 50, 'error': str(e)}
    
    async def check_stochastic_momentum(self, klines: List) -> Tuple[bool, Dict]:
        """Анализ стохастического осциллятора"""
        try:
            if len(klines) < 20:
                return False, {'error': 'Недостаточно данных'}
            
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            
            # Расчет %K (стохастик)
            period = 14
            smoothing = 3
            
            stoch_k = []
            for i in range(period, len(closes)):
                high_max = max(highs[i-period:i])
                low_min = min(lows[i-period:i])
                current_close = closes[i]
                
                if high_max != low_min:
                    k_value = 100 * (current_close - low_min) / (high_max - low_min)
                    stoch_k.append(k_value)
            
            if len(stoch_k) < smoothing:
                return False, {'error': 'Недостаточно данных для расчета'}
            
            # Сглаживание %K для получения %D
            stoch_d = []
            for i in range(smoothing-1, len(stoch_k)):
                d_value = np.mean(stoch_k[i-smoothing+1:i+1])
                stoch_d.append(d_value)
            
            if not stoch_k or not stoch_d:
                return False, {'error': 'Ошибка расчета стохастика'}
            
            current_k = stoch_k[-1]
            current_d = stoch_d[-1]
            
            # Бычьи условия:
            # 1. %K выше %D
            # 2. Оба вышли из зоны перепроданности (<20)
            # 3. Находятся в зоне накопления (20-80)
            k_above_d = current_k > current_d
            out_of_oversold = current_k > 20 and current_d > 20
            in_accumulation = 20 <= current_k <= 80
            
            passed = k_above_d and out_of_oversold and in_accumulation
            
            return passed, {
                'k_value': current_k,
                'd_value': current_d,
                'k_above_d': k_above_d,
                'out_of_oversold': out_of_oversold,
                'in_accumulation': in_accumulation
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа стохастика: {e}")
            return False, {'error': str(e)}
    
    # 🏛️ Фундаментальные и рыночные факторы
    
    async def check_market_cap_volume(self, symbol: str) -> Tuple[bool, Dict]:
        """Анализ соотношения объема и капитализации"""
        try:
            ticker_response = self.bybit_client.get_tickers(
                category="linear",
                symbol=symbol
            )
            
            if not ticker_response['result']['list']:
                return False, {'error': 'Нет данных тикера'}
            
            ticker_data = ticker_response['result']['list'][0]
            volume_24h = float(ticker_data.get('volume24h', 0))
            turnover_24h = float(ticker_data.get('turnover24h', 0))
            
            if volume_24h == 0:
                return False, {'error': 'Нулевой объем'}
            
            # Отношение оборота к объему (средняя цена сделки)
            avg_trade_size = turnover_24h / volume_24h if volume_24h > 0 else 0
            
            # Проверяем ликвидность и значимость токена
            sufficient_volume = volume_24h > 100000  # Минимум 100k контрактов
            sufficient_turnover = turnover_24h > 1000000  # Минимум 1M USDT оборота
            reasonable_avg_trade = 100 <= avg_trade_size <= 10000  # Разумный размер сделки
            
            passed = sufficient_volume and sufficient_turnover and reasonable_avg_trade
            
            return passed, {
                'volume_24h': volume_24h,
                'turnover_24h': turnover_24h,
                'avg_trade_size': avg_trade_size,
                'sufficient_volume': sufficient_volume,
                'sufficient_turnover': sufficient_turnover,
                'reasonable_avg_trade': reasonable_avg_trade
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа капитализации: {e}")
            return False, {'error': str(e)}
    
    async def check_relative_volatility(self, symbol: str, current_atr: float, current_price: float) -> Tuple[bool, Dict]:
        """Анализ волатильности относительно BTC"""
        try:
            # Получаем данные BTC для сравнения
            btc_data = self.bybit_client.get_kline(category="linear",
                symbol='BTCUSDT',
                interval='15',
                limit=100
            )
            
            if not btc_data['result']['list']:
                return True, {'error': 'Нет данных BTC', 'assume_ok': True}
            
            btc_klines = btc_data['result']['list']
            btc_closes = [float(k[4]) for k in btc_klines]
            btc_highs = [float(k[2]) for k in btc_klines]
            btc_lows = [float(k[3]) for k in btc_klines]
            
            # Расчет ATR для BTC
            btc_atr = self._calculate_atr(btc_highs, btc_lows, btc_closes)
            btc_price = btc_closes[-1] if btc_closes else current_price
            
            if btc_atr == 0 or btc_price == 0:
                return True, {'error': 'Ошибка расчета BTC ATR', 'assume_ok': True}
            
            # Процентные ATR
            symbol_atr_percent = (current_atr / current_price) * 100
            btc_atr_percent = (btc_atr / btc_price) * 100
            
            if btc_atr_percent == 0:
                return True, {'error': 'Нулевая волатильность BTC', 'assume_ok': True}
            
            # Относительная волатильность
            relative_volatility = symbol_atr_percent / btc_atr_percent
            
            # Приемлемый диапазон: 0.5x - 2.0x от волатильности BTC
            passed = 0.5 <= relative_volatility <= 2.0
            
            return passed, {
                'symbol_atr_percent': symbol_atr_percent,
                'btc_atr_percent': btc_atr_percent,
                'relative_volatility': relative_volatility,
                'volatility_ratio': relative_volatility
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа относительной волатильности: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    def _calculate_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Расчет Average True Range"""
        if len(highs) < period or len(lows) < period or len(closes) < period:
            return 0
        
        true_ranges = []
        for i in range(1, len(highs)):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i-1])
            low_close = abs(lows[i] - closes[i-1])
            
            true_range = max(high_low, high_close, low_close)
            true_ranges.append(true_range)
        
        if len(true_ranges) < period:
            return 0
        
        atr = np.mean(true_ranges[-period:])
        return atr
    
    # ⏰ Временные и сезонные факторы
    
    async def check_trading_session(self) -> Tuple[bool, Dict]:
        """Анализ оптимального времени для торговли"""
        try:
            now = datetime.now()
            current_hour = now.hour
            current_weekday = now.weekday()
            current_minute = now.minute
            
            # Избегаем выходных
            if current_weekday >= 5:  # Суббота, воскресенье
                return False, {
                    'session': 'weekend',
                    'optimal': False,
                    'reason': 'Выходные - низкая ликвидность'
                }
            
            # Определяем торговые сессии (UTC)
            sessions = {
                'asian': (0, 8),      # Азиатская сессия
                'european': (8, 16),  # Европейская сессия  
                'american': (16, 24)  # Американская сессия
            }
            
            current_session = None
            for session, (start, end) in sessions.items():
                if start <= current_hour < end:
                    current_session = session
                    break
            
            # Наиболее оптимальные периоды:
            # - Начало европейской сессии (8-10 UTC)
            # - Перекрытие европейской и американской (14-16 UTC)
            # - Начало американской сессии (16-18 UTC)
            optimal_periods = [
                (8, 10),   # Начало Европы
                (14, 16),  # Перекрытие Европа/Америка
                (16, 18)   # Начало Америки
            ]
            
            in_optimal_period = any(start <= current_hour < end for start, end in optimal_periods)
            
            # Избегаем первых и последних минут часа (волатильность из-за закрытия свечей)
            avoid_volatile_minutes = current_minute <= 5 or current_minute >= 55
            
            passed = in_optimal_period and not avoid_volatile_minutes
            
            return passed, {
                'current_session': current_session,
                'current_hour': current_hour,
                'current_minute': current_minute,
                'in_optimal_period': in_optimal_period,
                'avoid_volatile_minutes': avoid_volatile_minutes,
                'optimal_periods': optimal_periods
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа торговой сессии: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    async def check_seasonal_pattern(self) -> Tuple[bool, Dict]:
        """Анализ сезонных паттернов"""
        try:
            now = datetime.now()
            current_month = now.month
            current_day = now.day
            current_weekday = now.weekday()
            
            # Известные бычьи периоды:
            bullish_months = [1, 4, 10, 11]  # Январь, апрель, октябрь, ноябрь
            bearish_months = [2, 6, 9]       # Февраль, июнь, сентябрь
            
            # Эффекты начала/конца месяца
            month_end_effect = current_day >= 25 or current_day <= 7
            
            # Эффект "первого дня месяца"
            first_week_effect = current_day <= 7
            
            # Пятничный эффект (профит-тейкинг перед выходными)
            friday_effect = current_weekday == 4  # Пятница
            
            # Бычьи условия:
            month_positive = current_month in bullish_months
            month_neutral = current_month not in bearish_months
            timing_positive = month_end_effect or first_week_effect
            avoid_friday = not friday_effect
            
            passed = (month_positive or (month_neutral and timing_positive)) and avoid_friday
            
            return passed, {
                'current_month': current_month,
                'current_day': current_day,
                'month_positive': month_positive,
                'month_neutral': month_neutral,
                'timing_positive': timing_positive,
                'avoid_friday': avoid_friday,
                'bullish_months': bullish_months,
                'bearish_months': bearish_months
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа сезонности: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    # 🎯 Психологические уровни
    
    async def check_psychological_levels(self, current_price: float) -> Tuple[bool, Dict]:
        """Анализ психологических ценовых уровней"""
        try:
            if current_price <= 0:
                return False, {'error': 'Некорректная цена'}
            
            # Определяем порядок цены для выбора соответствующих уровней
            price_order = 10 ** (np.floor(np.log10(current_price)))
            
            # Основные психологические уровни
            if current_price < 1:
                levels = [0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
            elif current_price < 10:
                levels = [1, 2, 5, 10]
            elif current_price < 100:
                levels = [10, 20, 50, 100]
            elif current_price < 1000:
                levels = [100, 200, 500, 1000]
            else:
                levels = [1000, 2000, 5000, 10000, 20000, 50000]
            
            # Находим ближайшие уровни
            distances = [abs(current_price - level) for level in levels]
            min_distance = min(distances) if distances else current_price
            nearest_level = levels[distances.index(min_distance)] if distances else 0
            # Процентное расстояние до ближайшего уровня
            distance_percent = (min_distance / current_price) * 100
            
            # Должны быть достаточно далеко от психологических уровней (>0.5%)
            passed = distance_percent > 0.5
            
            return passed, {
                'current_price': current_price,
                'nearest_level': nearest_level,
                'distance_percent': distance_percent,
                'all_levels': levels,
                'min_distance': min_distance
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа психологических уровней: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    async def check_large_orders_clusters(self, orderbook: Dict, current_price: float) -> Tuple[bool, Dict]:
        """Анализ скоплений крупных ордеров"""
        try:
            bids = orderbook.get('b', [])
            asks = orderbook.get('a', [])
            
            if not bids or not asks:
                return False, {'error': 'Нет данных стакана'}
            
            # Порог для крупных ордеров (в USDT)
            large_order_threshold = 10000
            
            # Анализируем крупные ордера в стакане
            large_bids = 0
            large_asks = 0
            
            for bid in bids:
                price = float(bid[0])
                volume = float(bid[1])
                order_size = price * volume
                if order_size >= large_order_threshold:
                    large_bids += 1
            
            for ask in asks:
                price = float(ask[0])
                volume = float(ask[1])
                order_size = price * volume
                if order_size >= large_order_threshold:
                    large_asks += 1
            
            # Баланс крупных ордеров
            total_large_orders = large_bids + large_asks
            if total_large_orders == 0:
                return True, {
                    'large_bids': 0,
                    'large_asks': 0,
                    'order_imbalance': 0,
                    'assume_ok': True,
                    'reason': 'Нет крупных ордеров'
                }
            
            order_imbalance = (large_bids - large_asks) / total_large_orders
            
            # Преимущество крупных покупателей
            passed = order_imbalance > 0
            
            return passed, {
                'large_bids': large_bids,
                'large_asks': large_asks,
                'total_large_orders': total_large_orders,
                'order_imbalance': order_imbalance,
                'threshold': large_order_threshold
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа крупных ордеров: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    # 🔄 Корреляционный анализ
    
    async def check_correlation_with_btc(self, symbol: str, klines: List) -> Tuple[bool, Dict]:
        """Анализ корреляции с Bitcoin"""
        try:
            if symbol == 'BTCUSDT':
                return True, {'correlation': 1.0, 'is_btc': True}
            
            if len(klines) < 50:
                return True, {'error': 'Недостаточно данных', 'assume_ok': True}
            
            # Получаем данные BTC за тот же период
            btc_response = self.bybit_client.get_kline(
                category="linear",
                symbol='BTCUSDT',
                interval='15',
                limit=len(klines)
            )
            
            if not btc_response['result']['list']:
                return True, {'error': 'Нет данных BTC', 'assume_ok': True}
            
            btc_klines = btc_response['result']['list']
            
            # Приводим к одинаковой длине
            min_length = min(len(klines), len(btc_klines))
            symbol_closes = [float(k[4]) for k in klines[-min_length:]]
            btc_closes = [float(k[4]) for k in btc_klines[-min_length:]]
            
            if len(symbol_closes) < 30:
                return True, {'error': 'Недостаточно данных для корреляции', 'assume_ok': True}
            
            # Рассчитываем корреляцию
            correlation = np.corrcoef(symbol_closes, btc_closes)[0, 1]
            
            if np.isnan(correlation):
                return True, {'error': 'Ошибка расчета корреляции', 'assume_ok': True}
            
            # Идеальная корреляция для альткоинов: 0.3-0.7
            # Слишком низкая - нет связи с рынком
            # Слишком высокая - нет альфа (двигается как BTC)
            passed = 0.3 <= correlation <= 0.7
            
            return passed, {
                'correlation': correlation,
                'data_points': len(symbol_closes),
                'correlation_strength': self._get_correlation_strength(correlation)
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа корреляции: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    def _get_correlation_strength(self, correlation: float) -> str:
        """Определение силы корреляции"""
        abs_corr = abs(correlation)
        if abs_corr < 0.3:
            return "Слабая"
        elif abs_corr < 0.7:
            return "Умеренная"
        else:
            return "Сильная"
    
    # 🛡️ Управление рисками (расширенное)
    
    async def check_var_risk(self, klines: List, confidence_level: float = 0.95) -> Tuple[bool, Dict]:
        """Анализ риска по Value at Risk"""
        try:
            if len(klines) < 30:
                return True, {'error': 'Недостаточно данных', 'assume_ok': True}
            
            closes = [float(k[4]) for k in klines]
            
            # Рассчитываем логарифмические доходности
            returns = []
            for i in range(1, len(closes)):
                ret = np.log(closes[i] / closes[i-1])
                returns.append(ret)
            
            if not returns:
                return True, {'error': 'Не удалось рассчитать доходности', 'assume_ok': True}
            
            # Расчет VaR (параметрический метод)
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            
            # Z-score для доверительного уровня
            from scipy import stats
            z_score = stats.norm.ppf(1 - confidence_level)
            
            # Однодневный VaR
            var_1d = abs(mean_return + z_score * std_return)
            
            # Приемлемый риск: не более 5% за день
            max_acceptable_var = 0.05
            passed = var_1d <= max_acceptable_var
            
            return passed, {
                'var_1d': var_1d,
                'var_1d_percent': var_1d * 100,
                'confidence_level': confidence_level,
                'mean_return': mean_return,
                'std_return': std_return,
                'max_acceptable_var': max_acceptable_var
            }
            
        except Exception as e:
            logger.error(f"Ошибка расчета VaR: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    async def check_max_drawdown(self, klines: List) -> Tuple[bool, Dict]:
        """Анализ исторической максимальной просадки"""
        try:
            if len(klines) < 20:
                return True, {'error': 'Недостаточно данных', 'assume_ok': True}
            
            closes = [float(k[4]) for k in klines]
            
            # Расчет максимальной просадки
            peak = closes[0]
            max_drawdown = 0
            drawdowns = []
            
            for price in closes:
                if price > peak:
                    peak = price
                drawdown = (peak - price) / peak
                drawdowns.append(drawdown)
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
            
            # Приемлемая просадка: не более 25%
            max_acceptable_drawdown = 0.25
            passed = max_drawdown <= max_acceptable_drawdown
            
            return passed, {
                'max_drawdown': max_drawdown,
                'max_drawdown_percent': max_drawdown * 100,
                'current_drawdown': drawdowns[-1] if drawdowns else 0,
                'max_acceptable_drawdown': max_acceptable_drawdown,
                'data_points': len(closes)
            }
            
        except Exception as e:
            logger.error(f"Ошибка расчета просадки: {e}")
            return True, {'error': str(e), 'assume_ok': True}

# 🎯 Утилиты для работы с расширенным чеклистом

def get_advanced_checklist_weights() -> Dict[str, float]:
    """Веса для расширенного чеклиста"""
    return {
        # Технический анализ
        'multi_timeframe_alignment': 0.08,
        'rsi_divergence': 0.06,
        'volume_clusters': 0.05,
        'multi_timeframe_rsi': 0.06,
        'stochastic_momentum': 0.05,
        
        # Фундаментальные факторы
        'market_cap_volume': 0.05,
        'relative_volatility': 0.04,
        
        # Временные факторы
        'trading_session': 0.04,
        'seasonal_pattern': 0.03,
        
        # Психологические уровни
        'psychological_levels': 0.04,
        'large_orders_clusters': 0.04,
        
        # Корреляционный анализ
        'correlation_with_btc': 0.05,
        
        # Управление рисками
        'var_risk': 0.03,
        'max_drawdown': 0.03
    }

def calculate_advanced_score(check_results: Dict) -> Tuple[float, Dict]:
    """Расчет общего score для расширенного чеклиста"""
    try:
        weights = get_advanced_checklist_weights()
        total_score = 0
        detailed_scores = {}
        
        for check_name, result in check_results.items():
            weight = weights.get(check_name, 0)
            passed = result.get('passed', False)
            score = weight if passed else 0
            total_score += score
            detailed_scores[check_name] = {
                'weight': weight,
                'passed': passed,
                'score': score,
                'details': result.get('details', {})
            }
        
        return total_score, detailed_scores
        
    except Exception as e:
        logger.error(f"Ошибка расчета расширенного score: {e}")
        return 0, {}