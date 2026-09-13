import requests
import json
import time
from typing import Dict, Tuple

class CourseSelection:
    def __init__(self, cookies: Dict[str, str], token: str, ticket: str, student_code: str):
        """
        初始化选课客户端
        
        Args:
            cookies: Cookie信息
            token: 用户token
            ticket: 用户ticket
            student_code: 学生学号
        """
        self.cookies = cookies
        self.token = token
        self.ticket = ticket
        self.student_code = student_code
        
        self.CLASS_TYPES = {
            "major": "TJKC",     # 主修课程
            "elective": "XGXK",  # 选修课程
            "physical": "TYKC",  # 体育课程
            "program": "FANKC",   # 方案内课程
            "all": "QXKC"        # 全校课表查询
        }

        # 教学班所在目录页码缓存与调试信息
        self._page_cache = {}
        self._drop_endpoint = None
        self._all_course_choice = None
        self.last_raw = ""
        self.last_scan_info = None

        self.batch_code, self.batch_info = self.get_available_batch(student_code)
        self.TERM_PREFIX = self.get_course_prefix(student_code, self.batch_code)

    def select_course(self, class_code: str, course_type: str) -> str:
        """按“课程代码”（课程号+班号，不含学期前缀）选课。"""
        return self.select_teaching_class(f"{self.TERM_PREFIX}{class_code}", course_type)

    def select_teaching_class(self, teaching_class_id: str, course_type: str) -> str:
        """按完整教学班ID选课，返回服务器提示信息。"""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "token": self.token
        }
        
        if course_type == "physical":
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.87 Safari/537.36"
        
        data = {
            "addParam": json.dumps({
                "data": {
                    "operationType": "1",
                    "studentCode": self.student_code,
                    "electiveBatchCode": self.batch_code,
                    "teachingClassId": teaching_class_id,
                    "isMajor": "1",
                    "campus": "1",
                    "teachingClassType": self.CLASS_TYPES[course_type]
                }
            })
        }
        
        response = requests.post(
            "https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/elective/volunteer.do",
            headers=headers,
            cookies=self.cookies,
            data=data,
            timeout=15,
        )
        self.last_raw = response.text
        
        try:
            return response.json().get("msg", "")
        except Exception:
            return response.text

    def get_person(self) -> dict:
        headers = {
            "Referer": f"http://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do?ticket={self.ticket}"
        }
        
        timestamp = int(time.time() * 1000)
        response = requests.get(
            f"https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/publicinfo/onlineUsers.do?timestamp={timestamp}",
            headers=headers,
            cookies=self.cookies
        )
        
        result = response.json()
        return result.get('data', {}).get('onlineUsers', 0)

    def get_batch_info(self, student_code: str) -> dict:
        response = self._get_raw_batch_info(student_code)
        if response['code'] != '1':
            raise Exception(f"获取选课批次信息失败：{response['msg']}")
            
        data = response['data']
        student_info = {
            'name': data['name'],
            'student_code': data['code'],
            'college': data['collegeName'],
            'department': data['departmentName'],
            'class_name': data['schoolClassName'],
            'grade': data['grade'],
            'campus': data['campusName']
        }
        
        batch_list = []
        for batch in data['electiveBatchList']:
            batch_info = {
                'batch_code': batch['code'],
                'name': batch['name'],
                'term': batch['schoolTerm'],
                'term_name': batch['schoolTermName'],
                'begin_time': batch['beginTime'],
                'end_time': batch['endTime'],
                'can_select': batch['canSelect'] == '1',
                'course_types': {
                    'major': batch['displayTJKC'] == '1', 
                    'program': batch['displayFANKC'] == '1', 
                    'physical': batch['displayTYKC'] == '1', 
                    'elective': batch['displayXGXK'] == '1' 
                }
            }
            batch_list.append(batch_info)
        
        return {
            'student': student_info,
            'batches': batch_list
        }

    def _get_raw_batch_info(self, student_code: str) -> dict:
        headers = {
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "token": self.token,
            "Referer": f"https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do?ticket={self.ticket}"
        }
        
        timestamp = int(time.time() * 1000)
        response = requests.get(
            f"https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/student/{student_code}.do?timestamp={timestamp}",
            headers=headers,
            cookies=self.cookies
        )
        
        return response.json()

    def major_course(self, class_code: str) -> str:
        return self.select_course(class_code, "major")
        
    def elective_course(self, class_code: str) -> str:
        return self.select_course(class_code, "elective")
        
    def physical_course(self, class_code: str) -> str:
        return self.select_course(class_code, "physical")
        
    def program_course(self, class_code: str) -> str:
        return self.select_course(class_code, "program")

    def get_available_batch(self, student_code: str) -> Tuple[str, dict]:
        batch_info = self.get_batch_info(student_code)
        
        for batch in batch_info['batches']:
            if (batch['can_select'] and 
                "本科生选课" in batch['name'] and 
                any(batch['course_types'].values())):
                return batch['batch_code'], batch
                
        raise Exception("未找到可用的选课批次")

    def get_course_prefix(self, student_code: str, batch_code: str, course_type: str = "major") -> str:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "token": self.token,
            "Referer": f"https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/*default/grablessons.do?token={self.token}"
        }
        
        query_data = {
            "data": {
                "studentCode": student_code,
                "campus": "1",
                "electiveBatchCode": batch_code,
                "isMajor": "1",
                "teachingClassType": self.CLASS_TYPES[course_type],
                "checkConflict": "2",
                "checkCapacity": "2",
                "queryContent": ""
            },
            "pageSize": "10",
            "pageNumber": "0",
            "order": ""
        }
        
        data = {
            "querySetting": json.dumps(query_data)
        }
        
        response = requests.post(
            "https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/elective/recommendedCourse.do",
            headers=headers,
            cookies=self.cookies,
            data=data
        )
        
        result = response.json()
        if result.get("code") != "1":
            raise Exception(f"获取课程信息失败：{result.get('msg')}")
        
        data_list = result.get("dataList", [])
        if not data_list:
            raise Exception("未找到任何课程信息")
        
        first_course = data_list[0]
        first_class = first_course.get("tcList", [])[0]
        teaching_class_id = first_class.get("teachingClassID", "")
        
        prefix = teaching_class_id.split(first_course["courseNumber"])[0]
        return prefix

    # ---------------- 教学班查询 / 已选课程 / 退课（按老师选班与自动换班） ----------------

    COURSE_QUERY_ENDPOINTS = {
        "major": "elective/recommendedCourse.do",
        "elective": "elective/programCourse.do",
        "physical": "elective/programCourse.do",
        "program": "elective/programCourse.do",
        "all": "elective/queryCourse.do",
    }

    # “全校课表查询”使用独立接口与类型代码（已实测确认）
    ALL_COURSE_CANDIDATES = [
        ("elective/queryCourse.do", "QXKC"),
    ]

    def _elective_headers(self, course_type: str = None) -> Dict[str, str]:
        """选修课相关接口的公共请求头。"""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "token": self.token,
            "Referer": f"https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/*default/grablessons.do?token={self.token}",
        }
        if course_type == "physical":
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.87 Safari/537.36"
        return headers

    def _query_catalog_raw(self, endpoint: str, teaching_class_type: str, page: int,
                           query_content: str = "") -> list:
        """按指定接口与教学班类型查询一页课程目录。"""
        data = {
            "studentCode": self.student_code,
            "campus": "1",
            "electiveBatchCode": self.batch_code,
            "isMajor": "1",
            "teachingClassType": teaching_class_type,
            "queryContent": query_content or "",
        }
        query_data = {
            "data": data,
            "pageSize": "10",
            "pageNumber": str(page),
            "order": "",
        }
        # 全校课表查询接口(queryCourse.do)不接受 checkConflict/checkCapacity/orderBy
        if endpoint != "elective/queryCourse.do":
            data["checkConflict"] = "2"
            data["checkCapacity"] = "2"
            query_data["orderBy"] = "courseNumber"
        response = requests.post(
            f"https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/{endpoint}",
            headers=self._elective_headers(),
            cookies=self.cookies,
            data={"querySetting": json.dumps(query_data)},
            timeout=15,
        )
        try:
            result = response.json()
        except Exception:
            return []
        if str(result.get("code")) != "1":
            raise Exception(f"查询课程失败：{result.get('msg')}")
        return result.get("dataList") or []

    @staticmethod
    def _wrap_flat_rows(rows: list) -> list:
        """把“一行一个教学班”的扁平结果包装成 course(含 tcList) 结构，便于统一处理。"""
        if not rows:
            return []
        if any(isinstance(r, dict) and r.get("tcList") for r in rows):
            return rows
        return [{
            "courseNumber": row.get("courseNumber"),
            "courseName": row.get("courseName"),
            "tcList": [row],
        } for row in rows]

    def query_catalog_page(self, course_type: str, page: int, query_content: str = "") -> list:
        """查询某一页课程目录，返回 dataList（每项含 tcList 教学班列表）。

        query_content 会作为 querySetting.data.queryContent 提交，可用于“搜索课程”。
        对于“全校课程”会自动探测可用的接口与类型代码（不含方案外 FAWKC）。
        """
        if course_type == "all":
            if self._all_course_choice:
                endpoint, teaching_class_type = self._all_course_choice
                return self._wrap_flat_rows(
                    self._query_catalog_raw(endpoint, teaching_class_type, page, query_content)
                )
            errors = []
            for endpoint, teaching_class_type in self.ALL_COURSE_CANDIDATES:
                try:
                    data_list = self._query_catalog_raw(endpoint, teaching_class_type, page, query_content)
                except Exception as exc:
                    errors.append(f"{teaching_class_type}: {exc}")
                    continue
                self._all_course_choice = (endpoint, teaching_class_type)
                return self._wrap_flat_rows(data_list)
            raise Exception("全校课表查询失败（" + "；".join(errors) + "）")

        endpoint = self.COURSE_QUERY_ENDPOINTS.get(course_type, "elective/programCourse.do")
        return self._query_catalog_raw(endpoint, self.CLASS_TYPES[course_type], page, query_content)

    @staticmethod
    def _course_matches(course: dict, keyword: str) -> bool:
        keyword = (keyword or "").strip().lower()
        if not keyword:
            return True
        number = str(course.get("courseNumber") or "").lower()
        name = str(course.get("courseName") or "").lower()
        if number and (number in keyword or keyword in number):
            return True
        if name and keyword in name:
            return True
        for tc in course.get("tcList") or []:
            if keyword in str(tc.get("teachingClassID") or "").lower():
                return True
        return False

    def _remember_course_page(self, course_type: str, course: dict, page: int, query_content: str) -> None:
        number = str(course.get("courseNumber") or "")
        if number:
            self._page_cache[(course_type, number)] = (page, query_content)

    def find_courses(self, keyword: str, course_type: str = "major", max_pages: int = 30) -> list:
        """按课程号/课程名/选课课号检索课程（结果含全部 tcList 教学班）。"""
        keyword = (keyword or "").strip()
        keyword_lower = keyword.lower()
        matches = []
        pages_scanned = 0
        first_number = ""
        last_number = ""
        errors = []

        # 方式一：把关键字作为 queryContent 直接检索（平台“搜索课程”即如此）
        if keyword:
            for page in range(8):
                try:
                    data_list = self.query_catalog_page(course_type, page, query_content=keyword)
                except Exception as exc:
                    errors.append(str(exc))
                    break
                pages_scanned += 1
                if not data_list:
                    break
                if not first_number:
                    first_number = str(data_list[0].get("courseNumber") or "")
                last_number = str(data_list[-1].get("courseNumber") or "")
                for course in data_list:
                    if self._course_matches(course, keyword):
                        matches.append(course)
                        self._remember_course_page(course_type, course, page, keyword)
                if len(data_list) < 10:
                    break
            if matches:
                self.last_scan_info = {
                    "pages": pages_scanned,
                    "first": first_number,
                    "last": last_number,
                    "matches": len(matches),
                    "mode": "search",
                }
                return matches

        # 方式二：翻页 + 本地匹配
        for page in range(max_pages):
            try:
                data_list = self.query_catalog_page(course_type, page)
            except Exception as exc:
                errors.append(str(exc))
                break
            pages_scanned += 1
            if not data_list:
                break
            if not first_number:
                first_number = str(data_list[0].get("courseNumber") or "")
            last_number = str(data_list[-1].get("courseNumber") or "")
            for course in data_list:
                if self._course_matches(course, keyword):
                    matches.append(course)
                    self._remember_course_page(course_type, course, page, "")
            if matches and keyword_lower and last_number.lower() > keyword_lower:
                break
            if len(data_list) < 10:
                break

        self.last_scan_info = {
            "pages": pages_scanned,
            "first": first_number,
            "last": last_number,
            "matches": len(matches),
            "errors": errors[:2],
        }
        return matches

    @staticmethod
    def _first(tc: dict, *keys):
        """按候选字段名依次取值（兼容不同平台/版本的字段命名）。"""
        for key in keys:
            value = tc.get(key)
            if value not in (None, ""):
                return value
        return ""

    @staticmethod
    def _is_choose(tc: dict) -> bool:
        """判断该教学班是否为“我已选”。"""
        for key in ("isChoose", "is_choose", "isSelect", "selected"):
            value = tc.get(key)
            if value is None:
                continue
            if str(value).strip().lower() in ("1", "true", "yes", "已选"):
                return True
        return False

    @classmethod
    def teaching_class_status(cls, tc: dict) -> dict:
        """解析单个教学班的教师、时间地点与余量信息（兼容常见字段别名）。"""
        capacity = str(cls._first(tc, "classCapacity", "capacity", "limitNum", "classCapacityName"))
        selected = str(cls._first(tc, "numberOfSelected", "selectedNum", "numberOfSelectedName"))
        is_full = str(cls._first(tc, "isFull", "full"))
        is_conflict = str(cls._first(tc, "isConflict", "conflict"))
        available = is_full != "1"
        if capacity.isdigit() and selected.isdigit() and int(selected) >= int(capacity):
            available = False
        return {
            "teaching_class_id": str(cls._first(tc, "teachingClassID", "teachingClassId")),
            "teacher": str(cls._first(tc, "teacherName", "teacher", "teacherNames")).split("|")[0],
            "place": str(cls._first(tc, "teachingPlace", "classTime", "place")),
            "course_index": str(cls._first(tc, "courseIndex", "classIndex")),
            "capacity": capacity,
            "selected": selected,
            "is_full": is_full,
            "is_conflict": "1" if is_conflict in ("1", "true", "True") else "",
            "is_choose": "1" if cls._is_choose(tc) else "",
            "has_capacity_info": bool(capacity or selected or is_full),
            "available": available,
        }

    def selected_teaching_class_in_course(self, course: dict, target_id: str = ""):
        """用课程目录里各教学班的 isChoose 标记，判断我当前选的是哪个班。返回 (ID, 教师)。"""
        chosen = None
        for tc in (course or {}).get("tcList") or []:
            if not self._is_choose(tc):
                continue
            tc_id = str(self._first(tc, "teachingClassID", "teachingClassId"))
            teacher = str(self._first(tc, "teacherName", "teacher", "teacherNames"))
            if tc_id == target_id:
                return tc_id, teacher
            if chosen is None:
                chosen = (tc_id, teacher)
        return chosen if chosen else (None, "")

    def find_selected_teaching_class(self, course_type: str, course_code: str):
        """查找我当前在“指定课程代码”下已选的教学班（用于换掉另一门课程）。返回 (ID, 教师)。"""
        course_code = (course_code or "").strip()
        if not course_code:
            return None, ""

        # 先在给定类别目录里找（用 isChoose 标记）
        types = [course_type] + [t for t in self.CLASS_TYPES if t != course_type]
        for order, t in enumerate(types):
            max_pages = 30 if order == 0 else 10
            try:
                for course in self.find_courses(course_code, t, max_pages=max_pages):
                    tc_id, teacher = self.selected_teaching_class_in_course(course, "")
                    if tc_id:
                        return tc_id, teacher
            except Exception:
                continue

        # 兜底：读已选课程列表，用课程号前缀匹配
        try:
            rows = self.get_selected_courses()
        except Exception:
            rows = []
        for row in rows:
            row_id = str(row.get("teachingClassID") or row.get("teachingClassId") or "")
            row_number = str(row.get("courseNumber") or "")
            if row_id and row_number and course_code.startswith(row_number):
                return row_id, str(row.get("teacherName") or "")
        return None, ""

    def find_teaching_class(self, teaching_class_id: str, course_type: str, keyword: str = "", max_pages: int = 30):
        """查找指定教学班的实时状态，返回 (状态dict, 所属课程dict)，未找到返回 (None, None)。"""
        cache_key = (course_type, teaching_class_id)
        cached = self._page_cache.get(cache_key)
        if cached is not None:
            page, query_content = cached
            for course in self.query_catalog_page(course_type, page, query_content=query_content):
                for tc in course.get("tcList") or []:
                    if str(self._first(tc, "teachingClassID", "teachingClassId")) == teaching_class_id:
                        return self.teaching_class_status(tc), course
            # 缓存已失效，退回全量搜索

        keyword = keyword or teaching_class_id
        for course in self.find_courses(keyword, course_type, max_pages=max_pages):
            for tc in course.get("tcList") or []:
                if str(self._first(tc, "teachingClassID", "teachingClassId")) == teaching_class_id:
                    number = str(course.get("courseNumber") or "")
                    if (course_type, number) in self._page_cache:
                        self._page_cache[cache_key] = self._page_cache[(course_type, number)]
                    return self.teaching_class_status(tc), course
        return None, None

    def get_selected_courses(self) -> list:
        """获取当前已选课程列表。"""
        timestamp = int(time.time() * 1000)
        response = requests.post(
            "https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/elective/courseResult.do"
            f"?timestamp={timestamp}&studentCode={self.student_code}",
            headers=self._elective_headers(),
            cookies=self.cookies,
            timeout=15,
        )
        try:
            result = response.json()
        except Exception:
            return []
        return result.get("dataList") or []

    def _post_add_param(self, endpoint: str, data: dict, course_type: str = None) -> str:
        """以 addParam 形式提交一个选课/退课请求，返回服务器 msg。"""
        response = requests.post(
            f"https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/{endpoint}",
            headers=self._elective_headers(course_type),
            cookies=self.cookies,
            data={"addParam": json.dumps({"data": data})},
            timeout=15,
        )
        self.last_raw = response.text
        try:
            result = response.json()
        except Exception:
            return response.text
        return result.get("msg") or result.get("message") or str(result)

    def drop_course(self, teaching_class_id: str, course_type: str = "major") -> str:
        """退掉指定教学班。

        平台接口 deleteVolunteer.do 为 GET，且必须携带查询参数 deleteParam（JSON 字符串）。
        这里按常见格式自适应几种 payload，命中“成功”即返回。
        """
        base = {
            "studentCode": self.student_code,
            "electiveBatchCode": self.batch_code,
            "teachingClassId": teaching_class_id,
            "isMajor": "1",
            "campus": "1",
            "teachingClassType": self.CLASS_TYPES[course_type],
        }
        payloads = [
            {"data": dict(base, operationType="2")},
            {"data": base},
            dict(base, operationType="2"),
            {"data": {
                "studentCode": self.student_code,
                "electiveBatchCode": self.batch_code,
                "teachingClassId": teaching_class_id,
                "teachingClassType": self.CLASS_TYPES[course_type],
            }},
        ]
        last_msg = ""
        responses = []
        for payload in payloads:
            delete_param = json.dumps(payload)
            response = requests.get(
                "https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/elective/deleteVolunteer.do",
                headers=self._elective_headers(course_type),
                cookies=self.cookies,
                params={"deleteParam": delete_param},
                timeout=15,
            )
            self.last_raw = response.text
            try:
                result = response.json()
                msg = result.get("msg") or result.get("message") or ""
            except Exception:
                msg = response.text
            responses.append(f"{delete_param} -> {msg}")
            last_msg = msg
            if "成功" in msg:
                self.last_raw = " | ".join(responses)
                return msg
        self.last_raw = " | ".join(responses)
        return last_msg

    def code_from_teaching_class_id(self, teaching_class_id: str) -> str:
        """由完整教学班ID还原出 config 中使用的“课程代码”（去掉学期前缀）。"""
        if self.TERM_PREFIX and teaching_class_id.startswith(self.TERM_PREFIX):
            return teaching_class_id[len(self.TERM_PREFIX):]
        return teaching_class_id

    def teaching_class_id_from_code(self, code: str) -> str:
        """把 config 中的“课程代码”补全为完整教学班ID。"""
        return f"{self.TERM_PREFIX}{code}"