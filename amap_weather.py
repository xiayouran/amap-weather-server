# -*- coding: utf-8 -*-
# Author  : liyanpeng
# Email   : yanpeng.li@cumt.edu.cn
# Datetime: 2025/3/29 18:44
# Filename: amap_weather.py
from typing import Any
import pandas as pd
import json
import yaml
from urllib import parse
import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP(name="amap-weather", dependencies=["pandas", "openpyxl"])


def city_code_data() -> dict:
    """解析高德地图提供的城市编码表
    https://a.amap.com/lbs/static/code_resource/AMap_adcode_citycode.zip
    """
    df = pd.read_excel('AMap_adcode_citycode.xlsx', dtype='str')
    df = df.fillna(value='')
    city_map = {}
    province = ''
    city = ''
    for row in df.values[1:]:
        city_name, ad_code, city_code = row
        if ad_code[2:] == '0000':   # 省/直辖市
            province = city_name
            city_map[province] = ad_code
            city = ''
        elif ad_code[-2:] == '00':  # 市
            city = city_name
            city_map[province + city] = ad_code
        else:   # 区/县
            city_map[province + city + city_name] = ad_code

    # with open('city-map.json', 'w', encoding='utf-8') as f:
    #     json.dump(city_map, f, indent=4, ensure_ascii=False)

    return city_map


def config_data() -> dict:
    with open('config.yaml', 'r', encoding='utf-8') as file:
        data = yaml.load(file, Loader=yaml.FullLoader)
    return data


# Constants
CITY_CODE_MAP = city_code_data()
CONFIG_MAP = config_data()
AMAP_API_BASE = "https://restapi.amap.com/v3/weather/weatherInfo?city={adccode}&key={api_key}&extensions={type}"
AMAP_API_KEY = CONFIG_MAP.get('api_key')


async def make_amap_request(url: str) -> dict[str, Any] | None:
    """处理向高德-天气查询API发送的请求"""
    headers = {
        "Accept": "application/json"
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None


def format_weather_info(response: dict) -> str:
    """将天气信息格式化为可读字符串"""
    if 'lives' in response:
        weather_info = response.get('lives')
        for weather in weather_info:
            return f"""
地区：{weather.get('province')} {weather.get('city')}
天气现象：{weather.get('weather')}
实时气温：{weather.get('temperature')}摄氏度
风向描述：{weather.get('winddirection')}
风力级别：{weather.get('windpower')}级
空气湿度：{weather.get('humidity')}
数据发布时间：{weather.get('reporttime')}
""".strip()
    else:
        weather_info = response.get('forecasts')
        content_list = [
            f"""
地区：{weather_info.get('province')} {weather_info.get('city')}
预报发布时间：{weather_info.get('reporttime')}
"""
        ]
        for weather in weather_info.get('casts'):
            content_list.append(
                f"""
日期：{weather.get('date')} 星期{weather.get('week')}
白天天气现象：{weather.get('dayweather')}
晚上天气现象：{weather.get('nightweather')}
白天温度：{weather.get('daytemp')}
晚上温度：{weather.get('nighttemp')}
白天风向：{weather.get('daywind')}
晚上风向：{weather.get('nightwind')}
白天风力：{weather.get('daypower')}级
晚上风力：{weather.get('nightpower')}级
"""
            )
        return '---'.join(content_list).strip()


@mcp.tool(name='get_lives', description='获取某个地点的实况天气')
async def get_lives(adccode: str) -> str:
    """获取某个地点的实况天气"""

    url = AMAP_API_BASE.format(api_key=AMAP_API_KEY, adccode=adccode, type='base')
    data = await make_amap_request(url)

    if not data:
        return "Unable to fetch lives data for this city."

    weather_text = format_weather_info(data)
    return weather_text


@mcp.tool(name='get_forecast', description='获取某个地点的预报天气')
async def get_forecast(adccode: str) -> str:
    """获取某个地点的预报天气"""

    url = AMAP_API_BASE.format(api_key=AMAP_API_KEY, adccode=adccode, type='base')
    data = await make_amap_request(url)

    if not data:
        return "Unable to fetch forecast data for this city."

    weather_text = format_weather_info(data)
    return weather_text


@mcp.resource(uri='city://all', name='get_city_info', mime_type='application/json')
def get_city_code() -> dict:
    """所有城市编码数据"""
    return CITY_CODE_MAP


@mcp.resource('city://{city_name}')
def get_city_code_by_cityname(city_name: str) -> str:
    """获取指定城市的城市编码"""
    city_name = parse.unquote(city_name, encoding='utf-8')
    return CITY_CODE_MAP.get(city_name, '')


@mcp.prompt(name='city-identification', description='城市识别指令')
def llm_prompt(query: str) -> str:
    """城市识别指令"""
    prompt = """请严格按照以下规则识别文本中的行政区划信息：

1. **识别原则**
- 输出格式：三级列表[省/直辖市, 地级市/直辖市辖区, 区/县/县级市]
- 直辖市处理：当识别到北京/上海/天津/重庆时，省字段填"XX市"，地级市字段留空
- 同名处理：当区县级名称重复时(如朝阳区)，需列出所有可能的组合
- 名称补全：县级单位需补全行政后缀(县/区/市)，省级和地级单位无需补全后缀

2. **解析流程**
- 精准识别文本中出现的所有地理名称
- 确定名称的行政级别：
   - 省级：含"省"字或四大直辖市简称
   - 地级：含"市"且非直辖市的独立地名
   - 县级：含"区/县/市"或著名县级地名(如昆山、义乌等)
- 向上补全省市信息：
   - 县级单位必须补全对应的地级市和省级
   - 地级单位必须补全省级
- 冲突处理：当县级名称存在多地重复时，列出所有可能组合

3. **输出示例**
输入："朝阳医院在哪里" → 
[["北京市", "", "朝阳区"], ["吉林省", "长春市", "朝阳区"], ["辽宁省", "朝阳市", "双塔区"]]

输入："昆山天气" → 
[["江苏省", "苏州市", "昆山市"]]

输入："渝北区美食" → 
[["重庆市", "", "渝北区"]]

4. **格式要求**
必须严格按以下 JSON 格式输出：
```json
{
  "output": 输出结果,
  "explain": 简明理由（30字内）
}

需要识别的文本："""
    return prompt + query


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
