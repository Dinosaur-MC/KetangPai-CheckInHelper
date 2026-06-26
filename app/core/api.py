import time
import random
import logging
import requests
from typing import Optional, List
from pydantic import BaseModel, Field
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# -------------------- 基础配置 --------------------
API_BASE = "https://openapiv5.ketangpai.com"
CHECKIN_BASE = "https://w.ketangpai.com"

# 通用请求头（可被具体函数覆盖）
COMMON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "DNT": "1",
    "Origin": "https://w.ketangpai.com",
    "Referer": "https://w.ketangpai.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.5845.163 Mobile Safari/537.36 MicroMessenger/8.0.50.2700(0x28003237) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
}


# -------------------- Pydantic 模型 --------------------
# 1. 登录接口
class LoginRequest(BaseModel):
    email: str  # 邮箱/手机号
    password: str
    remember: str = "1"
    source_type: int = 1
    reqtimestamp: int = Field(default_factory=lambda: int(time.time() * 1000))


class LoginData(BaseModel):
    token: Optional[str] = None
    uid: Optional[str] = None
    bindWechat: Optional[bool] = None


class LoginResponse(BaseModel):
    status: int
    code: int
    message: str
    data: LoginData = Field(default_factory=LoginData)


# 2. 获取用户信息接口
class GetUserInfoRequest(BaseModel):
    secondDomain: str = ""
    reqtimestamp: int = Field(default_factory=lambda: int(time.time() * 1000))


class AdditionInfo(BaseModel):
    enrolltime: str = ""
    grade: str = ""
    classno: str = ""


class UserInfo(BaseModel):
    id: str
    username: str
    avatar: str
    department: Optional[str] = None
    usertype: str
    email: Optional[str] = None
    stno: str
    school: str
    account: str
    mobile: str
    teachcourseid: Optional[str] = None
    notify1: str
    notify2: str
    notify3: str
    notify4: str
    isenterprise: str
    atteststate: int
    attestInfo: List = []  # JSON 中是空数组，可根据实际补充类型
    isvip: int
    openid: str
    unionid: str
    wechat_nikename: str  # 注意：字段名与接口返回一致（typo）
    teachcourse: List = []
    majors: List = []
    majorsV2: List = []
    additionInfo: AdditionInfo
    userScore: int
    coid: int
    endtime: int
    i18nSwitchEnabled: int


class GetUserInfoResponse(BaseModel):
    status: int
    code: int
    message: str
    data: UserInfo


# 3. 二维码签到接口
class QRCheckInRequest(BaseModel):
    type: int = 2
    ticketid: str
    expire: int
    sign: str
    courseid: str
    randNum: str


# 4. GPS / 数字码签到接口
class CheckInRequest(BaseModel):
    id: str  # 考勤记录ID
    courseid: str = ""  # 课程ID（用于查询绑定账号）
    code: str = ""  # 数字考勤码
    latitude: str = ""  # 纬度
    longitude: str = ""  # 经度


# 5. 签到结果
class CheckInResult(BaseModel):
    email: str
    success: bool
    message: str
    code: int = 0  # 课堂派业务码: 30319=二维码过期, 30322=考勤已结束, 30324=重复签到


# GPS 位置错误码（签到失败因位置不在允许范围内）
POSITION_ERROR_CODES: set[int] = {30315, 30320, 30321, 30323}
POSITION_ERROR_KEYWORDS: tuple[str, ...] = ("位置", "范围", "距离", "定位")


def is_position_error(result: CheckInResult) -> bool:
    """判断签到失败是否因位置不正确引起。"""
    if not result.success:
        if result.code in POSITION_ERROR_CODES:
            return True
        msg = result.message or ""
        for kw in POSITION_ERROR_KEYWORDS:
            if kw in msg:
                return True
    return False


def _extract_gps(resp: dict | list) -> tuple[str | None, str | None]:
    """从建筑 GPS API 响应（已解包的 data 层）中提取经纬度。

    兼容格式：
    - {"lat": "…", "lng": "…"}
    - {"latitude": "…", "longitude": "…"}
    - [{"lat": "…", "lng": "…"}, ...]
    """
    if isinstance(resp, list):
        if len(resp) > 0 and isinstance(resp[0], dict):
            resp = resp[0]
        else:
            return None, None

    if not isinstance(resp, dict):
        return None, None

    lat = None
    for key in ("lat", "latitude"):
        if key in resp and resp[key]:
            lat = str(resp[key])
            break

    lng = None
    for key in ("lng", "longitude"):
        if key in resp and resp[key]:
            lng = str(resp[key])
            break

    return lat, lng


# 5. 获取课程列表接口
class SemesterCourseListRequest(BaseModel):
    isstudy: str = "1"
    search: str = ""
    semester: str = "2026-2027"
    term: str = "1"
    reqtimestamp: int = Field(default_factory=lambda: int(time.time() * 1000))


class CourseItem(BaseModel):
    id: str  # 课程 ID
    code: str = ""
    course_name: str = Field(default="", alias="coursename")
    semester: str = ""
    term: str = ""


class SemesterCourseListResponse(BaseModel):
    status: int
    code: int
    message: str
    data: list[CourseItem] = []


# -------------------- 请求函数 --------------------
def build_session(token: Optional[str] = None) -> requests.Session:
    """创建带基础 headers 的 Session，可选注入 token"""
    s = requests.Session()
    s.headers.update(COMMON_HEADERS)
    if token:
        s.headers["token"] = token
    return s


class KetangPaiAPI:
    """
    ketangpai.com API

    :function login: 登录接口
    :function get_user_info: 获取用户信息接口
    :function qr_check_in: 二维码签到接口
    :function gps_check_in: GPS / 数字码签到接口
    """

    def __init__(self, email: str, password: str, token: Optional[str] = None):
        self.email = email
        self.password = password
        self.session = build_session(token)
        self.token = token
        self.user_info = None

    def close(self):
        self.session.close()

    def login(self) -> LoginResponse:
        """
        登录接口
        :param email: 邮箱/手机号
        :param password: 密码
        :return: 登录响应（包含 token、uid 等）
        :raises RuntimeError: 业务失败时抛出，消息包含 API 返回的错误原因
        """
        req = LoginRequest(email=self.email, password=self.password)
        resp = self.session.post(f"{API_BASE}/UserApi/login", json=req.model_dump())
        resp.raise_for_status()
        result = LoginResponse(**resp.json())
        # 检查业务状态：status!=1 或 token 为空视为登录失败
        if result.status != 1 or not result.data.token:
            msg = result.message or "登录失败（未知原因）"
            raise RuntimeError(msg)
        self.token = result.data.token
        self.session.headers["token"] = result.data.token
        return result

    def get_user_info(self) -> GetUserInfoResponse:
        """
        获取用户信息
        :return: 用户信息响应
        """
        req = GetUserInfoRequest()
        resp = self.session.post(
            f"{API_BASE}/UserApi/getUserInfo", json=req.model_dump()
        )
        resp.raise_for_status()
        return GetUserInfoResponse(**resp.json())

    def get_course_list(self) -> list[CourseItem]:
        """获取学期课程列表。"""
        req = SemesterCourseListRequest(
            reqtimestamp=int(time.time() * 1000) + random.randint(-100, 100),
        )
        resp = self.session.post(
            f"{API_BASE}/CourseApi/semesterCourseList",
            json=req.model_dump(),
        )
        resp.raise_for_status()
        data: dict = resp.json()
        if data.get("status") != 1:
            raise RuntimeError(
                f"获取课程列表失败：{data.get('message', 'Unknown error')}"
            )
        return [CourseItem(**item) for item in data.get("data", [])]

    def check_in_with_url(self, url: str) -> CheckInResult:
        query = parse_qs(urlparse(url).query)
        return self.qr_check_in(
            QRCheckInRequest(
                ticketid=query.get("ticketid", [""])[0],
                expire=query.get("expire", [0])[0],
                sign=query.get("sign", [""])[0],
                courseid=query.get("courseid", [""])[0],
                randNum=query.get("randNum", [""])[0],
            )
        )

    def get_attence_building_gps(self, attenceid: str) -> dict | list:
        """获取考勤关联的建筑 GPS 坐标。

        POST 请求，响应解包后返回 data 层。
        """
        try:
            resp = self.session.post(
                f"{API_BASE}/AttenceV2Api/getAttenceBuildingGps",
                json={"attenceid": attenceid},
            )
            resp.raise_for_status()
            j = resp.json()
            if j.get("status") == 1:
                return j.get("data", {})
            logger.warning("getAttenceBuildingGps status!=1 for %s: %s", attenceid, j.get("message"))
            return {}
        except requests.exceptions.RequestException as e:
            logger.warning("Failed to get building GPS for %s: %s", attenceid, e)
            return {}
        except ValueError:
            logger.warning("Invalid JSON response from getAttenceBuildingGps for %s", attenceid)
            return {}

    def get_attence_location(self, attenceid: str) -> dict:
        """获取考勤位置配置。

        POST 请求，响应包含考勤的定位设置（中心点坐标、签到半径等）。
        """
        try:
            resp = self.session.post(
                f"{API_BASE}/AttenceApi/getLocation",
                json={"attenceid": attenceid},
            )
            resp.raise_for_status()
            j = resp.json()
            if j.get("status") == 1:
                return j.get("data", {})
            logger.warning("getLocation status!=1 for %s: %s", attenceid, j.get("message"))
            return {}
        except requests.exceptions.RequestException as e:
            logger.warning("Failed to get location for %s: %s", attenceid, e)
            return {}
        except ValueError:
            logger.warning("Invalid JSON response from getLocation for %s", attenceid)
            return {}

    def get_not_finish_attence_student(self, courseid: str) -> list[dict]:
        """获取课程未完成的签到列表。

        POST /AttenceApi/getNotFinishAttenceStudent
        返回 data.lists，每项包含 id / type 等。
        type: 1=数字, 2=GPS, 3=二维码, 4=签入签出
        """
        try:
            resp = self.session.post(
                f"{API_BASE}/AttenceApi/getNotFinishAttenceStudent",
                json={"courseid": courseid, "reqtimestamp": int(time.time() * 1000)},
            )
            resp.raise_for_status()
            j = resp.json()
            if j.get("status") == 1:
                return (j.get("data") or {}).get("lists") or []
            return []
        except Exception as e:
            logger.warning("Failed to get not finish attence for %s: %s", courseid, e)
            return []

    def get_digit_attence(self, attence_id: str) -> str:
        """获取数字考勤码。

        POST /AttenceApi/getDigitAttence
        返回 data.data.code（数字签到码），失败返回空字符串。
        """
        try:
            resp = self.session.post(
                f"{API_BASE}/AttenceApi/getDigitAttence",
                json={"id": attence_id, "reqtimestamp": int(time.time() * 1000)},
            )
            resp.raise_for_status()
            j = resp.json()
            if j.get("status") == 1:
                inner = j.get("data") or {}
                return (inner.get("data") or {}).get("code") or ""
            return ""
        except Exception as e:
            logger.warning("Failed to get digit attence for %s: %s", attence_id, e)
            return ""

    def qr_check_in(self, data: QRCheckInRequest, client_ip: str = "") -> CheckInResult:
        """二维码签到接口（POST AttenceApi/AttenceResult，返回 JSON）。

        :param client_ip: 客户端真实 IP，将作为 X-Forward-For 请求头发送给课堂派。
        :return: 签到结果（success=True 表示签到成功）。
        """
        body = {
            "ticketid": data.ticketid,
            "expire": data.expire,
            "sign": data.sign,
            "reqtimestamp": int(time.time() * 1000),
        }
        extra_headers = {}
        if client_ip:
            extra_headers["X-Forward-For"] = client_ip
        try:
            resp = self.session.post(
                f"{API_BASE}/AttenceApi/AttenceResult",
                json=body,
                headers=extra_headers,
            )
            resp.raise_for_status()
            j: dict = resp.json()

            code = j.get("code", 0)
            message = j.get("message", "")

            # 重复签到 (30324) 视同成功
            if code == 30324:
                return CheckInResult(
                    email=self.email,
                    success=True,
                    message=message or "重复签到（已成功）",
                    code=code,
                )

            # data.state == 8 表示二维码签到成功
            if j.get("status") == 1:
                data_section = j.get("data") or {}
                if data_section.get("state") == 8:
                    return CheckInResult(
                        email=self.email,
                        success=True,
                        message="签到成功",
                        code=0,
                    )

            # 其他业务错误 → 失败，message 已有可读文本
            return CheckInResult(
                email=self.email,
                success=False,
                message=message or f"签到失败 (code={code})",
                code=code,
            )
        except requests.exceptions.RequestException as e:
            return CheckInResult(
                email=self.email,
                success=False,
                message=f"请求失败：{e}",
            )

    def gps_check_in(self, data: CheckInRequest, client_ip: str = "") -> CheckInResult:
        """GPS / 数字码签到接口（POST AttenceApi/checkin，返回 JSON）。

        当 latitude / longitude 为空时，自动调用课堂派的建筑 GPS
        接口获取坐标填充。

        :param client_ip: 客户端真实 IP，将作为 X-Forward-For 请求头发送给课堂派。
        :return: 签到结果（success=True 表示签到成功）。
        """
        latitude = data.latitude
        longitude = data.longitude

        # 自动获取建筑 GPS 坐标（返回值已解包为 data 层）
        if not latitude or not longitude:
            gps_resp = self.get_attence_building_gps(data.id)
            lat, lng = _extract_gps(gps_resp)
            if lat is not None:
                latitude = lat
            if lng is not None:
                longitude = lng

            # 兜底：从考勤位置配置获取
            if not latitude or not longitude:
                loc_resp = self.get_attence_location(data.id)
                loc_lat, loc_lng = _extract_gps(loc_resp)
                if loc_lat is not None:
                    latitude = latitude or loc_lat
                if loc_lng is not None:
                    longitude = longitude or loc_lng

        body = {
            "id": data.id,
            "code": data.code,
            "unusual": "",
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": "",
            "appid": "",
            "clienttype": 1,
            "reqtimestamp": int(time.time() * 1000),
        }
        extra_headers = {}
        if client_ip:
            extra_headers["X-Forward-For"] = client_ip
        try:
            resp = self.session.post(
                f"{API_BASE}/AttenceApi/checkin",
                json=body,
                headers=extra_headers,
            )
            resp.raise_for_status()
            j: dict = resp.json()

            code = j.get("code", 0)
            message = j.get("message", "")

            # 重复签到 (30324) 视同成功
            if code == 30324:
                return CheckInResult(
                    email=self.email,
                    success=True,
                    message=message or "重复签到（已成功）",
                    code=code,
                )

            # data.state == 1 表示 GPS/数字签到成功
            if j.get("status") == 1:
                data_section = j.get("data") or {}
                if data_section.get("state") == 1:
                    return CheckInResult(
                        email=self.email,
                        success=True,
                        message="签到成功",
                        code=0,
                    )

            # 其他业务错误 → 失败
            return CheckInResult(
                email=self.email,
                success=False,
                message=message or f"签到失败 (code={code})",
                code=code,
            )
        except requests.exceptions.RequestException as e:
            return CheckInResult(
                email=self.email,
                success=False,
                message=f"请求失败：{e}",
            )
