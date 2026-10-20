import httpx

from .config import now, settings

CITIES = {'杭州': (30.2741, 120.1551), '北京': (39.9042, 116.4074), '上海': (31.2304, 121.4737), '广州': (23.1291, 113.2644), '深圳': (22.5431, 114.0579), '成都': (30.5728, 104.0668), '武汉': (30.5928, 114.3055), '南京': (32.0603, 118.7969), '长沙': (28.2282, 112.9388), '昆明': (25.0389, 102.7183)}


async def get_weather(city):
    if not settings.weather_enabled:
        return {'available': False, 'reason': '天气工具已关闭，请补充当地实际天气。'}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            matched = next((k for k in CITIES if city in (k, k + '市')), None)
            if matched:
                latitude, longitude = CITIES[matched]
            else:
                geo = await client.get('https://geocoding-api.open-meteo.com/v1/search', params={'name': city, 'count': 1, 'language': 'zh', 'countryCode': 'CN'})
                geo.raise_for_status()
                found = geo.json().get('results', [])
                if not found:
                    return {'available': False, 'reason': '未匹配到城市，请提供更准确的城市名称。'}
                latitude, longitude = found[0]['latitude'], found[0]['longitude']
                matched = found[0]['name']
            response = await client.get('https://api.open-meteo.com/v1/forecast', params={'latitude': latitude, 'longitude': longitude, 'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max', 'timezone': 'Asia/Shanghai', 'forecast_days': 3})
            response.raise_for_status()
            data = response.json()['daily']
            days = [{'date': date, 'temperature_max': data['temperature_2m_max'][i], 'temperature_min': data['temperature_2m_min'][i], 'rain_probability': data['precipitation_probability_max'][i], 'wind_max': data['wind_speed_10m_max'][i]} for i, date in enumerate(data['time'])]
            return {'available': True, 'city': matched, 'provider': 'Open-Meteo', 'source_url': 'https://open-meteo.com/', 'queried_at': now().isoformat(), 'timezone': 'Asia/Shanghai', 'units': {'temperature': '°C', 'wind': 'km/h', 'rain_probability': '%'}, 'days': days}
    except (httpx.HTTPError, KeyError, ValueError):
        return {'available': False, 'reason': '天气服务暂不可用，请以现场天气补充判断。'}
