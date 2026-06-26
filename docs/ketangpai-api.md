# 课堂派 (KetangPai) API 文档

> Base URL: `https://openapiv5.ketangpai.com`
>
> 所有接口均使用 **POST** 方法，请求体为 JSON。
> 前端 axios 拦截器会自动注入 `reqtimestamp` 字段。

## 通用响应格式

```json
{
    "status": 1,       // 1=业务成功, 其他=业务失败
    "code": 0,         // 错误码（成功时为 0）
    "message": "",     // 提示消息
    "data": { ... }    // 业务数据
}
```

前端请求封装 (`813a` 模块) 在 `status === 1` 时会自动解包返回 `data` 层，
否则 reject 并弹出 `message` 提示。

## 1. 认证

### 1.1 登录

```
POST /UserApi/login
```

**请求体：**

| 字段           | 类型   | 必填 | 说明         |
| -------------- | ------ | ---- | ------------ |
| `email`        | string | 是   | 邮箱或手机号 |
| `password`     | string | 是   | 密码         |
| `remember`     | string | 否   | 默认 `"1"`   |
| `source_type`  | int    | 否   | 默认 `1`     |
| `reqtimestamp` | int    | 是   | 毫秒时间戳   |

**响应 `data` 字段：**

| 字段         | 类型   | 说明         |
| ------------ | ------ | ------------ |
| `token`      | string | 认证 token   |
| `uid`        | string | 用户 ID      |
| `bindWechat` | bool   | 是否绑定微信 |

### 1.2 获取用户信息

```
POST /UserApi/getUserInfo
```

**请求体：**

| 字段           | 类型   | 必填 | 说明       |
| -------------- | ------ | ---- | ---------- |
| `secondDomain` | string | 否   | 默认 `""`  |
| `reqtimestamp` | int    | 是   | 毫秒时间戳 |

**响应 `data` 字段:** 包含 `username`, `avatar`, `email`, `stno`, `school`, `mobile`, `account` 等。

## 2. 课程

### 2.1 获取学期课程列表

```
POST /CourseApi/semesterCourseList
```

**请求体：**

| 字段           | 类型   | 必填 | 说明                               |
| -------------- | ------ | ---- | ---------------------------------- |
| `isstudy`      | string | 否   | 默认 `"1"`                         |
| `search`       | string | 否   | 搜索关键字                         |
| `semester`     | string | 否   | 学期，如 `"2026-2027"`             |
| `term`         | string | 否   | 学期段，`"1"`=上学期, `"2"`=下学期 |
| `reqtimestamp` | int    | 是   | 毫秒时间戳                         |

**响应 `data` 字段：** 课程数组，每项包含 `id`, `coursename`, `code`, `semester`, `term`。

## 3. 签到 (Attendance)

### 3.1 获取课程未完成的签到列表

```
POST /AttenceApi/getNotFinishAttenceStudent
```

**请求体：**

| 字段           | 类型   | 必填 | 说明       |
| -------------- | ------ | ---- | ---------- |
| `courseid`     | string | 是   | 课程 ID    |
| `reqtimestamp` | int    | 是   | 毫秒时间戳 |

**响应 `data.lists`：** 未完成的签到数组，每项包含：

| 字段                  | 类型   | 说明                                                          |
| --------------------- | ------ | ------------------------------------------------------------- |
| `id`                  | string | 签到记录 ID                                                   |
| `type`                | string | 签到类型: `"1"`=数字, `"2"`=GPS, `"3"`=二维码, `"4"`=签入签出 |
| `title`               | string | 签到标题（日期）                                              |
| `createtime`          | int    | 发起时间（秒级时间戳）                                        |
| `signTime`            | int    | 签到时间                                                      |
| `state`               | int    | 签到状态: `0`=出勤, `1`=缺勤, `2`=迟到, `7`=早退              |
| `checkinover`         | int    | `0`=可签到, `1`=已结束                                        |
| `attenceCheckinState` | int    | 签入签出状态（type=4 时使用）                                 |
| `duration`            | string | 考勤时长                                                      |
| `checkouttime`        | string | 签出时间                                                      |

### 3.2 二维码签到

```
POST /AttenceApi/AttenceResult
```

**请求体：**

| 字段           | 类型         | 必填 | 说明                |
| -------------- | ------------ | ---- | ------------------- |
| `ticketid`     | string       | 是   | 二维码中的 ticketid |
| `expire`       | string / int | 是   | 过期时间戳          |
| `sign`         | string       | 是   | 签名                |
| `reqtimestamp` | int          | 否   | 拦截器自动注入      |

**成功标志：** `data.state == 8`

**错误码：**

| code  | 说明                   |
| ----- | ---------------------- |
| 30319 | 二维码已过期           |
| 30322 | 考勤已结束             |
| 30324 | 重复签到（可视为成功） |

### 3.3 GPS / 数字码签到

```
POST /AttenceApi/checkin
```

**请求体：**

| 字段           | 类型   | 必填 | 说明                                 |
| -------------- | ------ | ---- | ------------------------------------ |
| `id`           | string | 是   | 考勤记录 ID                          |
| `code`         | string | 否   | 数字考勤码（非数字签到时传空字符串） |
| `unusual`      | string | 否   | 异常标记，默认 `""`                  |
| `latitude`     | string | 否   | 纬度（GPS 签到必填，数字签到可空）   |
| `longitude`    | string | 否   | 经度                                 |
| `accuracy`     | string | 否   | 定位精度（米），默认 `""`            |
| `clienttype`   | int    | 否   | 客户端类型: `1`=微信/脚本            |
| `reqtimestamp` | int    | 否   | 拦截器自动注入                       |

**成功标志：** `data.state == 1`

**错误码：**

| code  | 说明                   |
| ----- | ---------------------- |
| 30315 | 位置不在签到范围内     |
| 30320 | GPS 定位失败           |
| 30321 | 位置异常               |
| 30322 | 考勤已结束             |
| 30323 | 签到范围错误           |
| 30324 | 重复签到（可视为成功） |

> **注意**：定位失败（位置错误）时返回的 code 在 30315–30323 范围内，
> 但非位置错误（如考勤已结束）的 code 为 30322。

### 3.4 获取数字考勤码

```
POST /AttenceApi/getDigitAttence
```

**请求体：**

| 字段           | 类型   | 必填 | 说明        |
| -------------- | ------ | ---- | ----------- |
| `id`           | string | 是   | 考勤记录 ID |
| `reqtimestamp` | int    | 是   | 毫秒时间戳  |

**响应路径：** `data.data.code` — 数字签到码（如 `"428571"`）

### 3.5 获取考勤建筑 GPS 坐标

```
POST /AttenceV2Api/getAttenceBuildingGps
```

**请求体：**

| 字段        | 类型   | 必填 | 说明        |
| ----------- | ------ | ---- | ----------- |
| `attenceid` | string | 是   | 考勤记录 ID |

**响应 `data` 字段（解包后）：**

```json
// 格式 1: 对象
{ "lat": "23.129163", "lng": "113.264435" }

// 格式 2: 数组（通常只有一个元素）
[ { "lat": "23.129163", "lng": "113.264435" } ]
```

### 3.6 获取考勤位置配置

```
POST /AttenceApi/getLocation
```

**请求体：**

| 字段        | 类型   | 必填 | 说明        |
| ----------- | ------ | ---- | ----------- |
| `attenceid` | string | 是   | 考勤记录 ID |

**响应 `data` 字段（解包后）：**

| 字段                         | 类型   | 说明           |
| ---------------------------- | ------ | -------------- |
| `lat` / `latitude`           | string | 中心点纬度     |
| `lng` / `longitude`          | string | 中心点经度     |
| `radius` / `range` / `scope` | int    | 围栏半径（米） |

### 3.7 获取二维码签到结果

```
POST /AttenceApi/getCheckInResult
```

**请求体：**

| 字段        | 类型   | 必填 | 说明        |
| ----------- | ------ | ---- | ----------- |
| `attenceid` | string | 是   | 考勤记录 ID |

> 此接口第三个参数传 `false`，表示不弹出错误提示。

### 3.8 历史考勤列表

```
POST /SummaryApi/attence
```

**请求体：**

| 字段       | 类型   | 必填 | 说明     |
| ---------- | ------ | ---- | -------- |
| `courseid` | string | 是   | 课程 ID  |
| `page`     | int    | 是   | 页码     |
| `size`     | int    | 是   | 每页条数 |

**响应 `data` 字段：**

| 字段              | 类型  | 说明                        |
| ----------------- | ----- | --------------------------- |
| `data`            | array | 考勤记录列表（同 3.1 格式） |
| `attenceCount`    | int   | 出勤数                      |
| `absentCount`     | int   | 缺勤数                      |
| `lateCount`       | int   | 迟到数                      |
| `leaveEarlyCount` | int   | 早退数                      |
| `sickLeaveCount`  | int   | 请假数                      |
| `total`           | int   | 总记录数                    |

## 4. 教师端（仅供参考）

以下接口在样本中可见，用于教师创建和管理考勤：

| 端点                                        | 用途             |
| ------------------------------------------- | ---------------- |
| `POST /AttenceApi/addNowsAttence`           | 创建传统考勤     |
| `POST /AttenceApi/addDigitAttence`          | 创建数字考勤     |
| `POST /AttenceApi/addGPSAttence`            | 创建 GPS 考勤    |
| `POST /AttenceApi/addAttence`               | 创建二维码考勤   |
| `POST /AttenceApi/addCheckinAttence`        | 创建签入签出考勤 |
| `POST /AttenceApi/overAttence`              | 结束考勤         |
| `POST /AttenceApi/delAttence`               | 删除考勤         |
| `POST /AttenceApi/renameAttence`            | 重命名考勤       |
| `POST /AttenceApi/updateState`              | 更新签到状态     |
| `POST /AttenceApi/updateDigitAttenceCode`   | 更新数字考勤码   |
| `POST /AttenceApi/getQrCode`                | 获取签到二维码   |
| `POST /AttenceV2Api/getAttenceStudentLists` | 获取考勤学生列表 |

## 5. 签到类型对照

| type 值 | 常量         | 说明           |
| ------- | ------------ | -------------- |
| `0`     | 传统考勤     | 点名式         |
| `1`     | 数字考勤     | 输入数字码     |
| `2`     | GPS 考勤     | 基于位置的签到 |
| `3`     | 二维码考勤   | 扫码签到       |
| `4`     | 签入签出考勤 | 需签入和签出   |

## 6. 签到状态对照

| state 值 | 说明             |
| -------- | ---------------- |
| `0`      | 出勤             |
| `1`      | 缺勤             |
| `2`      | 迟到             |
| `3`      | 请假             |
| `4`      | 事假             |
| `5`      | 病假             |
| `6`      | 公假             |
| `7`      | 早退             |
| `100`    | 可签到（未签到） |

## 7. 请求封装（前端 813a 模块）

```javascript
const u = (url, data, showAlert = true) =>
    axios.post(url, data).then((res) => {
        const t = res.data;
        if (t.status === 1) {
            return Promise.resolve(t.data); // 解包 data 层
        }
        if (showAlert) {
            alert(t.message); // 自动弹出错误
            if (t.code === 20003) {
                clearStorage();
                redirect("/login");
            }
            if (t.code === 30202) {
                /* 切换课程 */
            }
        }
        return Promise.reject(t);
    });
```

拦截器自动行为：

- 注入自定义 `token` 请求头（从 localStorage 读取 `token` 或 `token_for_login`）
- 请求体存在时自动注入 `reqtimestamp`

## 8. 响应 `data.state` 与 `status` 的区别

| 字段         | 层级 | 成功值             | 含义             |
| ------------ | ---- | ------------------ | ---------------- |
| `status`     | 顶层 | `1`                | API 调用是否成功 |
| `data.state` | 嵌套 | `1`(GPS) / `8`(QR) | 签到操作是否成功 |

签到接口的响应示例：

```json
// GPS 签到成功
{ "status": 1, "data": { "state": 1, "courseid": "..." }, "message": "" }

// 二维码签到成功
{ "status": 1, "data": { "state": 8 }, "message": "" }

// 签到失败（二维码过期）
{ "status": 0, "code": 30319, "message": "二维码已过期", "data": null }
```
