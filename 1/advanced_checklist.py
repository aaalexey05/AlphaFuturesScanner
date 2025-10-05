#!/usr/bin/env python3
"""
Расширенные пункты чеклиста для AlphaFutures Scanner
Дополнительные проверки для повышения качества торговых сигналов
"""

import numpy as np
import pandas as pd
import time
from datetime import datetime
from typing import Dict, List, Any, Tuple
import logging
import asyncio
from pybit.unified_trading import HTTP

logger = logging.getLogger(__name__)

class AdvancedChecklist:
    """Класс с расширенными проверками для чеклиста"""
    
    def __init__(self, bybit_client: HTTP):
        self.bybit_client = bybit_client
        self.kline_cache = {}  # Кэш для данных свечей
        self.orderbook_cache = {}  # Кэш для ордербука
        self.cache_timeout = 300  # 5 минут

    async def _get_kline(self, symbol: str, interval: str, limit: int) -> List:
        """Получение данных свечей с кэшированием"""
        cache_key = f"{symbol}_{interval}_{limit}"
        current_time = time.time()
        if cache_key in self.kline_cache:
            cached_data, timestamp = self.kline_cache[cache_key]
            if current_time - timestamp < self.cache_timeout:
                return cached_data
        response = self.bybit_client.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        klines = response['result']['list']
        self.kline_cache[cache_key] = (klines, current_time)
        return klines
    
    async def _get_orderbook(self, symbol: str, limit: int = 50) -> Dict:
        """Получение ордербука с кэшированием"""
        cache_key = f"{symbol}_orderbook_{limit}"
        current_time = time.time()
        if cache_key in self.orderbook_cache:
            cached_data, timestamp = self.orderbook_cache[cache_key]
            if current_time - timestamp < self.cache_timeout:
                return cached_data
        response = self.bybit_client.get_orderbook(category="linear", symbol=symbol, limit=limit)
        orderbook = response['result']
        self.orderbook_cache[cache_key] = (orderbook, current_time)
        return orderbook
    
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
            
            passed = (bullish_count / total_timeframes) >= 0.50
            
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
            klines = await self._get_kline(symbol, tf_code, 50)
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
            
            # Проверка валидности данных
            for bid in bids:
                if not isinstance(bid, list) or len(bid) < 2 or not all(isinstance(x, (str, float, int)) for x in bid):
                    return False, {'error': 'Некорректный формат данных бидов'}
            for ask in asks:
                if not isinstance(ask, list) or len(ask) < 2 or not all(isinstance(x, (str, float, int)) for x in ask):
                    return False, {'error': 'Некорректный формат данных асков'}
            
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
                tasks.append((task, tf_code, min_rsi, max_rsi))
            
            for task, tf_code, min_rsi, max_rsi in tasks:
                rsi, passed = await task
                results[tf_code] = {
                    'passed': passed,
                    'rsi': rsi,
                    'min_rsi': min_rsi,
                    'max_rsi': max_rsi
                }
                if passed:
                    passed_timeframes += 1
            
            # Требуется, чтобы хотя бы 2/3 таймфреймов прошли проверку
            passed = passed_timeframes >= 2
            
            return passed, {
                'passed_timeframes': passed_timeframes,
                'total_timeframes': len(timeframes),
                'timeframe_results': results
            }
            
        except Exception as e:
            logger.error(f"Ошибка проверки RSI на ТФ: {e}")
            return False, {'error': str(e)}
    
    async def _check_single_rsi(self, symbol: str, tf_code: str, min_rsi: float, max_rsi: float) -> Tuple[float, bool]:
        """Проверка RSI на одном таймфрейме"""
        try:
            klines = await self._get_kline(symbol, tf_code, 30)
            if len(klines) < 15:
                return 50, False
            closes = [float(k[4]) for k in klines]
            rsi = self._calculate_rsi(closes)
            passed = min_rsi <= rsi <= max_rsi
            return rsi, passed
        except Exception as e:
            logger.warning(f"Ошибка получения RSI для {symbol} на ТФ {tf_code}: {e}")
            return 50, False
    
    async def check_stochastic_momentum(self, klines: List) -> Tuple[bool, Dict]:
        """Проверка стохастического моментума"""
        try:
            if len(klines) < 20:
                return False, {'error': 'Недостаточно данных'}
            
            closes = [float(k[4]) for k in klines[-20:]]
            highs = [float(k[2]) for k in klines[-20:]]
            lows = [float(k[3]) for k in klines[-20:]]
            
            current_price = closes[-1]
            highest_high = max(highs[-14:])
            lowest_low = min(lows[-14:])
            
            k_value = 100 * (current_price - lowest_low) / (highest_high - lowest_low) if highest_high != lowest_low else 50
            d_value = np.mean([100 * (closes[i] - min(lows[i-14:i])) / (max(highs[i-14:i]) - min(lows[i-14:i])) 
                              for i in range(-3, 0)] if max(highs[-14:]) != min(lows[-14:]) else [50])
            
            k_above_d = k_value > d_value
            out_of_oversold = k_value > 20
            in_accumulation = 20 < k_value < 80
            
            passed = k_above_d and out_of_oversold and in_accumulation
            
            return passed, {
                'k_value': k_value,
                'd_value': d_value,
                'k_above_d': k_above_d,
                'out_of_oversold': out_of_oversold,
                'in_accumulation': in_accumulation
            }
            
        except Exception as e:
            logger.error(f"Ошибка проверки стохастического моментума: {e}")
            return False, {'error': str(e)}
    
    async def check_market_cap_volume(self, symbol: str) -> Tuple[bool, Dict]:
        """Проверка рыночной капитализации и объема"""
        try:
            ticker = self.bybit_client.get_tickers(category="linear", symbol=symbol)
            volume_24h = float(ticker['result']['list'][0]['volume24h'])
            turnover_24h = float(ticker['result']['list'][0]['turnover24h'])
            avg_trade_size = turnover_24h / volume_24h if volume_24h != 0 else 0
            
            sufficient_volume = volume_24h > 1000000
            sufficient_turnover = turnover_24h > 100000  # Снижено с более высокого значения
            reasonable_avg_trade = avg_trade_size > 0.01 if avg_trade_size != 0 else False
            
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
            logger.error(f"Ошибка проверки рыночной капитализации: {e}")
            return False, {'error': str(e)}
    
    async def check_relative_volatility(self, symbol: str, current_atr: float, current_price: float) -> Tuple[bool, Dict]:
        """Проверка относительной волатильности"""
        try:
            if current_price == 0 or current_atr == 0:
                return False, {'error': 'Недостаточно данных'}
            
            btc_ticker = self.bybit_client.get_tickers(category="linear", symbol="BTCUSDT")
            btc_price = float(btc_ticker['result']['list'][0]['lastPrice'])
            btc_klines = await self._get_kline("BTCUSDT", "15", 14)
            btc_closes = [float(k[4]) for k in btc_klines]
            btc_atr = np.mean([abs(float(k[2]) - float(k[3])) for k in btc_klines])
            
            symbol_atr_percent = (current_atr / current_price) * 100
            btc_atr_percent = (btc_atr / btc_price) * 100 if btc_price != 0 else 0
            volatility_ratio = symbol_atr_percent / btc_atr_percent if btc_atr_percent != 0 else float('inf')
            
            passed = 0.5 <= volatility_ratio <= 2.0
            
            return passed, {
                'symbol_atr_percent': symbol_atr_percent,
                'btc_atr_percent': btc_atr_percent,
                'relative_volatility': volatility_ratio,
                'volatility_ratio': volatility_ratio
            }
            
        except Exception as e:
            logger.error(f"Ошибка проверки относительной волатильности: {e}")
            return False, {'error': str(e)}
    
    # ⏰ Временные факторы    
    async def check_trading_session(self) -> Tuple[bool, Dict]:
        """Проверка текущей торговой сессии"""
        try:
            now = datetime.utcnow()
            hour = now.hour
            day = now.weekday()
            
            if day >= 5:  # Суббота/Воскресенье
                return True, {'session': 'weekend', 'optimal': True, 'low_volatility': False}  # Упрощение: passed=True
            
            # Определяем торговую сессию
            if 0 <= hour < 8:
                session = 'asian'
                optimal = False
                low_volatility = True
            elif 8 <= hour < 16:
                session = 'european'
                optimal = True
                low_volatility = False
            else:
                session = 'american'
                optimal = True
                low_volatility = False
            
            passed = optimal
            return passed, {
                'session': session,
                'optimal': optimal,
                'low_volatility': low_volatility
            }
            
        except Exception as e:
            logger.error(f"Ошибка проверки торговой сессии: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    async def check_seasonal_pattern(self) -> Tuple[bool, Dict]:
        """Проверка сезонных паттернов"""
        try:
            current_time = datetime.now()
            current_month = current_time.month
            current_day = current_time.day
            current_weekday = current_time.weekday()
            
            bullish_months = [1, 4, 10, 11]
            bearish_months = [2, 6, 9]
            
            month_positive = current_month in bullish_months
            month_neutral = current_month not in bearish_months
            timing_positive = 9 <= current_time.hour <= 20
            avoid_friday = current_weekday != 4
            
            passed = month_positive and month_neutral and timing_positive and avoid_friday
            
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
            logger.error(f"Ошибка проверки сезонных паттернов: {e}")
            return True, {'error': str(e), 'assume_ok': True}
    
    # 🎯 Психологические уровни
    
    async def check_psychological_levels(self, current_price: float) -> Tuple[bool, Dict]:
        """Проверка психологических уровней"""
        try:
            if current_price <= 0:
                return False, {'error': 'Недопустимая цена'}
            
            levels = [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100]
            distances = [abs(current_price - level) for level in levels]
            min_distance = min(distances)
            nearest_level = levels[distances.index(min_distance)]
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
            return False, {'error': str(e)}
    
    async def check_large_orders_clusters(self, orderbook: Dict, current_price: float) -> Tuple[bool, Dict]:
        """Анализ скоплений крупных ордеров"""
        try:
            bids = orderbook.get('b', [])
            asks = orderbook.get('a', [])
            
            if not bids or not asks:
                return False, {'error': 'Нет данных стакана'}
            
            # Проверка валидности данных
            for bid in bids:
                if not isinstance(bid, list) or len(bid) < 2 or not all(isinstance(x, (str, float, int)) for x in bid):
                    return False, {'error': 'Некорректный формат данных бидов'}
            for ask in asks:
                if not isinstance(ask, list) or len(ask) < 2 or not all(isinstance(x, (str, float, int)) for x in ask):
                    return False, {'error': 'Некорректный формат данных асков'}
            
            # Порог для крупных ордеров (в USDT)
            large_order_threshold = 10000
            
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
            return False, {'error': str(e)}
    
    # 🔄 Корреляционный анализ
    
    async def check_correlation_with_btc(self, symbol: str, klines: List) -> Tuple[bool, Dict]:
        """Анализ корреляции с Bitcoin"""
        try:
            if symbol == 'BTCUSDT':
                return True, {'correlation': 1.0, 'is_btc': True}
            
            if len(klines) < 50:
                return True, {'error': 'Недостаточно данных', 'assume_ok': True}
            
            btc_klines = await self._get_kline('BTCUSDT', '15', len(klines))
            if not btc_klines:
                return True, {'error': 'Нет данных BTC', 'assume_ok': True}
            
            min_length = min(len(klines), len(btc_klines))
            symbol_closes = [float(k[4]) for k in klines[-min_length:]]
            btc_closes = [float(k[4]) for k in btc_klines[-min_length:]]
            
            if len(symbol_closes) < 30:
                return True, {'error': 'Недостаточно данных для корреляции', 'assume_ok': True}
            
            correlation = np.corrcoef(symbol_closes, btc_closes)[0, 1]
            
            if np.isnan(correlation):
                return True, {'error': 'Ошибка расчета корреляции', 'assume_ok': True}
            
            passed = 0.3 <= correlation <= 0.7
            
            return passed, {
                'correlation': float(correlation),  # Конвертация numpy.float64
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
            
            returns = []
            for i in range(1, len(closes)):
                ret = np.log(closes[i] / closes[i-1])
                returns.append(ret)
            
            if not returns:
                return True, {'error': 'Не удалось рассчитать доходности', 'assume_ok': True}
            
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            
            from scipy import stats
            z_score = stats.norm.ppf(1 - confidence_level)
            
            var_1d = abs(mean_return + z_score * std_return)
            
            max_acceptable_var = 0.05
            passed = var_1d <= max_acceptable_var
            
            return passed, {
                'var_1d': float(var_1d),  # Конвертация numpy.float64
                'var_1d_percent': float(var_1d * 100),
                'confidence_level': confidence_level,
                'mean_return': float(mean_return),
                'std_return': float(std_return),
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
            
            max_acceptable_drawdown = 0.25
            passed = max_drawdown <= max_acceptable_drawdown
            
            return passed, {
                'max_drawdown': float(max_drawdown),  # Конвертация numpy.float64
                'max_drawdown_percent': float(max_drawdown * 100),
                'current_drawdown': float(drawdowns[-1] if drawdowns else 0),
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
        'multi_timeframe_alignment': 0.08,
        'rsi_divergence': 0.06,
        'volume_clusters': 0.05,
        'multi_timeframe_rsi': 0.06,
        'stochastic_momentum': 0.05,
        'market_cap_volume': 0.05,
        'relative_volatility': 0.04,
        'trading_session': 0.04,
        'seasonal_pattern': 0.03,
        'psychological_levels': 0.04,
        'large_orders_clusters': 0.04,
        'correlation_with_btc': 0.05,
        'var_risk': 0.03,
        'max_drawdown': 0.03
    }

def calculate_advanced_score(check_results: Dict) -> Tuple[float, Dict]:
    """Расчет общего score для расширенного чеклиста"""
    try:
        weights = get_advanced_checklist_weights()
        total_score = 0
        detailed_scores = {}
        
        for check_name in weights:
            if check_name not in check_results:
                logger.warning(f"Отсутствует результат для проверки {check_name}")
                detailed_scores[check_name] = {
                    'weight': weights[check_name],
                    'passed': False,
                    'score': 0,
                    'details': {'error': 'Результат не найден'}
                }
                continue
            
            result = check_results[check_name]
            weight = weights[check_name]
            passed = result.get('passed', False)
            score = weight if passed else 0
            total_score += score
            detailed_scores[check_name] = {
                'weight': weight,
                'passed': passed,
                'score': score,
                'details': result.get('details', {})
            }
        
        return float(total_score), detailed_scores  # Конвертация numpy.float64
        
    except Exception as e:
        logger.error(f"Ошибка расчета расширенного score: {e}")
        return 0, {}