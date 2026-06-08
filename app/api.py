import time
import random
import requests
from typing import Optional, List
from pydantic import BaseModel, Field
from urllib.parse import urlparse

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
    token: str
    uid: str
    bindWechat: bool


class LoginResponse(BaseModel):
    status: int
    code: int
    message: str
    data: LoginData


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


# 3. 签到接口
class CheckInRequest(BaseModel):
    type: int = 2
    ticketid: str
    expire: int
    sign: str
    courseid: str
    randNum: str


# 4. 签到结果（基于 HTML 页面关键词解析）
class CheckInResult(BaseModel):
    email: str
    success: bool
    message: str


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
    :function check_in: 签到接口
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
        """
        req = LoginRequest(email=self.email, password=self.password)
        resp = self.session.post(f"{API_BASE}/UserApi/login", json=req.model_dump())
        resp.raise_for_status()
        result = LoginResponse(**resp.json())
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
        data = resp.json()
        if data.get("status") != 1:
            raise RuntimeError(
                f"获取课程列表失败：{data.get('message', 'Unknown error')}"
            )
        return [CourseItem(**item) for item in data.get("data", [])]

    def check_in_with_url(self, url: str) -> CheckInResult:
        query = [
            {x.split("=")[0], x.split("=")[1]} for x in urlparse(url).query.split("&")
        ]
        return self.check_in(
            CheckInRequest(
                ticketid=query["ticketid"],
                expire=query["expire"],
                sign=query["sign"],
                courseid=query["courseid"],
                randNum=query["randNum"],
            )
        )

    def check_in(self, data: CheckInRequest) -> CheckInResult:
        """
        签到接口（GET 请求，返回 HTML）
        :param ticketid: 二维码 ticket ID
        :param expire: 过期时间戳
        :param sign: 签名
        :param courseid: 课程 ID
        :param randNum: 随机数
        :return: 解析后的签到结果
        """
        try:
            resp = self.session.get(
                f"{CHECKIN_BASE}/checkIn/checkinCodeResult", params=data.model_dump()
            )
            resp.raise_for_status()
            html = resp.text

            # 根据页面关键词判断结果
            if "签到成功" in html:
                return CheckInResult(success=True, message="签到成功")
            if "二维码已过期" in html:
                return CheckInResult(success=False, message="二维码已过期")
            if "考勤已结束" in html:
                return CheckInResult(success=False, message="考勤已结束")
            # 其它未知失败
            return CheckInResult(
                email=self.email, success=False, message="签到失败：其它错误"
            )
        except requests.exceptions.RequestException as e:
            return CheckInResult(
                email=self.email, success=False, message=f"签到失败：{e}"
            )
