import os
import sys

# PyInstaller 的 --windowed 模式下 sys.stdout/sys.stderr 为 None，
# 部分第三方库（如 selenium）向其中写入时会报错，这里兜底到空设备。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import tkinter as tk
from tkinter import ttk, messagebox
import ttkbootstrap as ttk
from login import Login, XKFW_INDEX_URL
from course_selection import *
import threading
import time
import datetime
import queue
import webbrowser

class CourseSelectionUI:
    def __init__(self, root, course_client, user_name):
        self.window = ttk.Toplevel(root)
        self.window.title("选课系统")
        self.window.geometry("480x1200")
        
        # 添加窗口关闭事件处理
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.course_client = course_client
        self.user_name = user_name
        self.running_threads = {}
        self.swap_status_time = {}
        self.swap_block_until = {}
        
        # 读取配置文件
        self.config = self.load_config()
        
        # 添加登录相关的属性
        self.root = root
        self.username = self.config.get('username', '')
        self.password = self.config.get('password', '')
        self.last_relogin_attempt = time.time()
        self.relogin_interval = 5  # 重新登录间隔（秒）
        self.is_logged_in = True   # 登录状态标志
        
        self.main_frame = ttk.Frame(self.window, padding="20")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        welcome_label = ttk.Label(
            self.main_frame,
            text=f"欢迎，{user_name}",
            font=("Microsoft YaHei UI", 16, "bold")
        )
        welcome_label.pack(pady=20)
        
        self.course_entries = []
        courses = self.config.get('courses', [])
        for i in range(4):
            frame = self.create_course_frame(i)
            frame.pack(fill=tk.X, pady=10)
            
            if i < len(courses):
                course = courses[i]
                self.course_entries[i]['entry'].insert(0, course.get('code', ''))
                self.course_entries[i]['type'].set(course.get('type', 'major'))
        
        self.info_text = tk.Text(
            self.main_frame,
            height=10,
            wrap=tk.WORD,
            font=("Microsoft YaHei UI", 10)
        )
        self.info_text.pack(fill=tk.BOTH, pady=10, expand=True)
        
        scrollbar = ttk.Scrollbar(self.info_text)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.info_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.info_text.yview)
        
        # 启动在线人数更新
        self.update_online_users()
        self.window.after(1000, self.update_online_users)
    
    def create_course_frame(self, index):
        """创建单个课程的输入框架"""
        frame = ttk.Labelframe(self.main_frame, text=f"课程 {index + 1}", padding="10")
        
        course_frame = ttk.Frame(frame)
        course_frame.pack(fill=tk.X, pady=5)
        
        course_label = ttk.Label(
            course_frame,
            text="课程代码：",
            font=("Microsoft YaHei UI", 10)
        )
        course_label.pack(side=tk.LEFT, padx=5)
        
        course_entry = ttk.Entry(course_frame, width=25)
        course_entry.pack(side=tk.LEFT, padx=5)

        course_type = tk.StringVar(value="major")
        type_frame = ttk.Frame(frame)
        type_frame.pack(fill=tk.X, pady=5)
        
        for type_text, type_value in [
            ("主修课程", "major"),
            ("选修课程", "elective"),
            ("体育课程", "physical"),
            ("方案内课程", "program"),
            ("全校课表查询", "all")
        ]:
            ttk.Radiobutton(
                type_frame,
                text=type_text,
                value=type_value,
                variable=course_type
            ).pack(side=tk.LEFT, padx=5)

        # 心仪教学班（换老师用）
        target_frame = ttk.Frame(frame)
        target_frame.pack(fill=tk.X, pady=5)

        target_label = ttk.Label(
            target_frame,
            text="心仪教学班：",
            font=("Microsoft YaHei UI", 10)
        )
        target_label.pack(side=tk.LEFT, padx=5)

        target_entry = ttk.Entry(target_frame, width=25)
        target_entry.pack(side=tk.LEFT, padx=5)

        auto_swap_var = tk.BooleanVar(value=False)
        auto_swap_check = ttk.Checkbutton(
            target_frame,
            text="有空位时自动换班",
            variable=auto_swap_var
        )
        auto_swap_check.pack(side=tk.LEFT, padx=5)

        # 需要换掉的现有课程（跨课程号换课，如 陆上赛艇 -> 长跑）
        replace_frame = ttk.Frame(frame)
        replace_frame.pack(fill=tk.X, pady=5)

        replace_label = ttk.Label(
            replace_frame,
            text="换掉现有课程：",
            font=("Microsoft YaHei UI", 10)
        )
        replace_label.pack(side=tk.LEFT, padx=5)

        replace_entry = ttk.Entry(replace_frame, width=25)
        replace_entry.pack(side=tk.LEFT, padx=5)

        replace_hint = ttk.Label(
            replace_frame,
            text="（可空；填写后先退掉该课程的现有教学班再换）",
            font=("Microsoft YaHei UI", 9)
        )
        replace_hint.pack(side=tk.LEFT, padx=5)

        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X, pady=5)
        
        start_button = ttk.Button(
            button_frame,
            text="开始抢课",
            style="primary.TButton",
            width=12,
            command=lambda: self.start_course_selection(index, course_entry, course_type, start_button)
        )
        start_button.pack(side=tk.LEFT, padx=5)
        
        stop_button = ttk.Button(
            button_frame,
            text="停止抢课",
            style="danger.TButton",
            width=12,
            command=lambda: self.stop_course_selection(index)
        )
        stop_button.pack(side=tk.LEFT, padx=5)

        query_button = ttk.Button(
            button_frame,
            text="查询教学班",
            width=12,
            command=lambda: self.open_query_dialog(index)
        )
        query_button.pack(side=tk.LEFT, padx=5)
        
        self.course_entries.append({
            'entry': course_entry,
            'type': course_type,
            'target': target_entry,
            'replace': replace_entry,
            'auto_swap': auto_swap_var,
            'start_button': start_button,
            'stop_button': stop_button
        })
        
        return frame
    
    def start_course_selection(self, index, entry, type_var, start_button):
        """开始抢课"""
        course_code = entry.get().strip()
        target_code = self.course_entries[index]['target'].get().strip()
        replace_code = self.course_entries[index]['replace'].get().strip()
        auto_swap = bool(self.course_entries[index]['auto_swap'].get())

        if not course_code and not (auto_swap and target_code):
            messagebox.showerror("错误", "请输入课程代码")
            return
        if auto_swap and not target_code:
            messagebox.showerror("错误", "已勾选自动换班，请填写心仪教学班（可点“查询教学班”获取）")
            return
        if replace_code and not auto_swap:
            messagebox.showerror("错误", "填写了“换掉现有课程”时，请同时勾选“有空位时自动换班”")
            return

        if index in self.running_threads:
            thread = self.running_threads[index]
            if thread is not None and thread.is_alive():
                return
        
        # 创建新线程
        thread = threading.Thread(
            target=self.course_selection_loop,
            args=(index, course_code, type_var.get(), target_code, auto_swap, replace_code),
            daemon=True
        )
        self.running_threads[index] = thread
        thread.start()
        
        start_button.config(state="disabled")
    
    def stop_course_selection(self, index):
        """停止抢课"""
        if index in self.running_threads:
            thread = self.running_threads[index]
            self.running_threads[index] = None
            self.course_entries[index]['start_button'].config(state="normal")
    
    def relogin(self):
        """重新登录"""
        current_time = time.time()
        if not self.is_logged_in and current_time - self.last_relogin_attempt < self.relogin_interval:
            return False

        self.last_relogin_attempt = current_time
        try:
            client = Login()
            success, message = client.login_process(self.username, self.password)
            
            if success:
                self.course_client = CourseSelection(
                    cookies=client.cookies,
                    token=client.token,
                    ticket=client.ticket,
                    student_code=self.username
                )
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.info_text.insert(tk.END, f"[{current_time}] 重新登录成功\n")
                self.info_text.see(tk.END)
                self.is_logged_in = True
                return True
            else:
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.info_text.insert(tk.END, f"[{current_time}] 重新登录失败：{message}，5秒后重试\n")
                self.info_text.see(tk.END)
                self.is_logged_in = False
                return False
        except Exception as e:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.info_text.insert(tk.END, f"[{current_time}] 重新登录错误：{str(e)}，5秒后重试\n")
            self.info_text.see(tk.END)
            self.is_logged_in = False
            return False

    def _log(self, message):
        """向信息区输出一条带时间的日志。"""
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.info_text.insert(tk.END, f"[{current_time}] {message}\n")
        self.info_text.see(tk.END)

    def _log_throttled(self, index, message, interval=10):
        """同一课程的状态日志最多每 interval 秒输出一次，避免刷屏。"""
        now = time.time()
        if now - self.swap_status_time.get(index, 0) >= interval:
            self.swap_status_time[index] = now
            self._log(message)

    def select_by_type(self, course_type, course_code):
        """按课程类型选课。"""
        if course_type == "major":
            return self.course_client.major_course(course_code)
        if course_type == "elective":
            return self.course_client.elective_course(course_code)
        if course_type == "physical":
            return self.course_client.physical_course(course_code)
        return self.course_client.program_course(course_code)

    def course_selection_loop(self, index, course_code, course_type, target_code="", auto_swap=False, replace_code=""):
        """抢课循环：普通模式按 course_code 抢；换班模式盯着心仪教学班，有空位就换。"""
        swap_mode = bool(auto_swap and target_code)
        if swap_mode:
            extra = f"，换掉现有课程：{replace_code}" if replace_code else ""
            self._log(f"课程 {index + 1} 已开启换班模式，心仪教学班：{target_code}{extra}")

        while index in self.running_threads and self.running_threads[index]:
            try:
                # 如果未登录，尝试重新登录
                if not self.is_logged_in:
                    if not self.relogin():
                        time.sleep(0.5)
                        continue

                if swap_mode:
                    if self.swap_course_round(index, course_code, course_type, target_code, replace_code):
                        break
                else:
                    result = self.select_by_type(course_type, course_code)
                    if "登录者身份" in result or "登录失效" in result:
                        self.is_logged_in = False
                        self._log("登录已失效，准备重新登录")
                        continue
                    self._log(f"课程 {course_code}：{result}")
                    if "成功" in result:
                        self.stop_course_selection(index)
                        break

            except Exception as e:
                self._log(f"发生错误：{str(e)}")

            time.sleep(0.3)

    def swap_course_round(self, index, course_code, course_type, target_code, replace_code=""):
        """换班模式的一轮。

        默认在同一课程号内换教学班；若填写了 replace_code，则换掉该课程的现有教学班，
        用于“不同课程号 N 选 1”的换课（如 陆上赛艇 -> 长跑）。返回 True 表示已完成。
        """
        if time.time() < self.swap_block_until.get(index, 0):
            return False

        client = self.course_client
        target_id = client.teaching_class_id_from_code(target_code)

        status, course = client.find_teaching_class(target_id, course_type, keyword=target_code)
        if status is None:
            # 当前批次目录里还没有它：尝试从“全校课程”确认它是否已存在，便于提示开放进度
            alt_status = None
            if course_type != "all":
                try:
                    alt_status, _alt_course = client.find_teaching_class(target_id, "all", keyword=target_code)
                except Exception:
                    alt_status = None
            if alt_status is not None:
                alt_teacher = alt_status["teacher"] or "教师待定"
                self._log_throttled(
                    index,
                    f"心仪教学班 {target_code}（{alt_teacher}）已可在“全校课程”中查到，"
                    f"但尚未出现在当前批次目录中，等待对当前批次开放…",
                )
                # 已在全校课程可见：每隔一段时间直接试选一次，抢在目录更新之前
                now = time.time()
                if now - self.swap_status_time.get(("try", index), 0) >= 10:
                    self.swap_status_time[("try", index)] = now
                    select_msg = client.select_teaching_class(target_id, course_type)
                    self._log(f"尝试直接选择心仪教学班 {target_code}：{select_msg}")
                    if "成功" in select_msg:
                        self.stop_course_selection(index)
                        return True
            else:
                info = client.last_scan_info or {}
                self._log_throttled(
                    index,
                    f"暂未查询到心仪教学班 {target_code}"
                    f"（已扫 {info.get('pages', 0)} 页，课程号范围 "
                    f"{info.get('first', '?')}~{info.get('last', '?')}），稍后重试",
                )
            return False

        teacher = status["teacher"] or "教师待定"

        current_id = None
        current_teacher = ""

        if replace_code:
            # 跨课程号换课：退掉“现有课程”的已选教学班（如 陆上赛艇 -> 长跑）
            current_id, current_teacher = client.find_selected_teaching_class(course_type, replace_code)
            if current_id is None:
                self._log_throttled(index, f"未找到现有课程 {replace_code} 的已选教学班，稍后重试")
                return False
        else:
            # 同课程号内换教学班
            current_id, current_teacher = client.selected_teaching_class_in_course(course, target_id)
            if current_id == target_id:
                self._log(f"已选中心仪教学班 {target_code}（{teacher}），换班完成")
                self.stop_course_selection(index)
                return True

            # 兜底：读取已选课程列表
            if current_id is None:
                try:
                    selected = client.get_selected_courses()
                except Exception as e:
                    selected = []
                    self._log_throttled(index, f"读取已选课程失败：{e}")
                ids = {str(r.get("teachingClassID") or r.get("teachingClassId") or "") for r in selected}
                if target_id in ids:
                    self._log(f"已选中心仪教学班 {target_code}（{teacher}），换班完成")
                    self.stop_course_selection(index)
                    return True
                course_number = str(course.get("courseNumber") or "")
                for row in selected:
                    if course_number and str(row.get("courseNumber") or "") == course_number:
                        current_id = str(row.get("teachingClassID") or row.get("teachingClassId") or "")
                        current_teacher = str(row.get("teacherName") or "")
                        break

        # 已在心仪教学班
        if current_id == target_id:
            self._log(f"已选中心仪教学班 {target_code}（{teacher}），换班完成")
            self.stop_course_selection(index)
            return True

        conflict_note = "（警告：该教学班与你现有课表时间冲突）" if status.get("is_conflict") == "1" else ""

        if not status["available"]:
            cap = f"{status['selected'] or '-'}/{status['capacity'] or '-'}"
            self._log_throttled(index, f"心仪教学班 {target_code}（{teacher}）暂无空位（{cap}），继续等待…")
            return False

        # 目标有空位，开始换班（平台要求先退后选）
        self._log(f"检测到空位！心仪教学班 {target_code}（{teacher}）可选中，开始换班{conflict_note}")
        if current_id:
            drop_msg = client.drop_course(current_id, course_type)
            self._log(f"退掉当前教学班（{current_teacher or current_id}）：{drop_msg}")
            dropped = "成功" in drop_msg
            if not dropped:
                # 服务器返回措辞不一定含“成功”，按目录实际状态再确认一次
                if replace_code:
                    verify_id, _verify_teacher = client.find_selected_teaching_class(course_type, replace_code)
                    dropped = verify_id is None
                else:
                    _st, course_after = client.find_teaching_class(target_id, course_type, keyword=target_code)
                    dropped = (course_after is not None
                               and client.selected_teaching_class_in_course(course_after, target_id)[0] is None)
                if dropped:
                    self._log("已按服务器实际状态确认：当前教学班已退掉")
            if not dropped:
                self._log(f"退课未成功，服务器原始返回：{client.last_raw[:400]}")
                self.swap_block_until[index] = time.time() + 30
                return False
        else:
            self._log("未检测到该课程已选教学班，直接尝试选择心仪教学班")

        select_msg = client.select_teaching_class(target_id, course_type)
        self._log(f"选择心仪教学班 {target_code}：{select_msg}")
        if "成功" in select_msg:
            self.stop_course_selection(index)
            return True

        # 再次按目录状态确认是否已选上
        st_final, _course_final = client.find_teaching_class(target_id, course_type, keyword=target_code)
        if st_final is not None and st_final["is_choose"] == "1":
            self._log("已按服务器实际状态确认：心仪教学班已选中")
            self.stop_course_selection(index)
            return True

        self._log(f"选课未成功，服务器原始返回：{client.last_raw[:400]}")
        # 换班失败：尝试把原教学班选回来，避免两头空
        if current_id:
            rollback_msg = client.select_teaching_class(current_id, course_type)
            self._log(f"尝试恢复原教学班（{current_teacher or current_id}）：{rollback_msg}")
        return False

    def open_query_dialog(self, index):
        """打开“查询教学班”窗口：按老师/时间/余量选择教学班。"""
        dialog = ttk.Toplevel(self.window)
        dialog.title(f"查询教学班 - 课程 {index + 1}")
        dialog.geometry("1000x620")
        dialog.transient(self.window)

        type_values = {
            "主修课程": "major",
            "选修课程": "elective",
            "体育课程": "physical",
            "方案内课程": "program",
            "全校课表查询": "all",
        }

        top = ttk.Frame(dialog, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="课程类型：").pack(side=tk.LEFT)
        type_box = ttk.Combobox(top, values=list(type_values.keys()), state="readonly", width=12)
        current_type = self.course_entries[index]['type'].get()
        selected_label = next((k for k, v in type_values.items() if v == current_type), "主修课程")
        type_box.set(selected_label)
        type_box.pack(side=tk.LEFT, padx=5)

        ttk.Label(top, text="课程号/课程名：").pack(side=tk.LEFT, padx=(10, 0))
        keyword_entry = ttk.Entry(top, width=30)
        keyword_entry.insert(0, self.course_entries[index]['entry'].get().strip())
        keyword_entry.pack(side=tk.LEFT, padx=5)

        search_button = ttk.Button(top, text="查询", style="primary.TButton", width=8)
        search_button.pack(side=tk.LEFT, padx=5)

        columns = ("course", "name", "teacher", "index", "place", "cap", "status")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=18)
        headings = {
            "course": "课程号", "name": "课程名", "teacher": "教师",
            "index": "教学班", "place": "上课时间/地点", "cap": "已选/容量", "status": "状态",
        }
        widths = {
            "course": 110, "name": 170, "teacher": 90,
            "index": 80, "place": 300, "cap": 80, "status": 70,
        }
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        rows_data = {}

        bottom = ttk.Frame(dialog, padding=10)
        bottom.pack(fill=tk.X)
        status_label = ttk.Label(bottom, text="", font=("Microsoft YaHei UI", 9))
        status_label.pack(side=tk.LEFT)

        def fill_code():
            sel = tree.selection()
            if not sel:
                status_label.config(text="请先选中一个教学班")
                return
            data = rows_data.get(sel[0])
            if not data:
                return
            self.course_entries[index]['entry'].delete(0, tk.END)
            self.course_entries[index]['entry'].insert(0, data['code'])
            status_label.config(text=f"已填入课程代码：{data['code']}")

        def fill_target():
            sel = tree.selection()
            if not sel:
                status_label.config(text="请先选中一个教学班")
                return
            data = rows_data.get(sel[0])
            if not data:
                return
            self.course_entries[index]['target'].delete(0, tk.END)
            self.course_entries[index]['target'].insert(0, data['code'])
            self.course_entries[index]['auto_swap'].set(True)
            status_label.config(text=f"已设为心仪教学班：{data['code']}（已勾选自动换班）")

        ttk.Button(bottom, text="设为目标(自动换班)", style="success.TButton",
                   command=fill_target).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom, text="填入课程代码", command=fill_code).pack(side=tk.RIGHT, padx=5)

        def render(rows, error):
            search_button.config(state="normal")
            tree.delete(*tree.get_children())
            rows_data.clear()
            if error is not None:
                status_label.config(text=f"查询失败：{error}")
                return
            if not rows:
                status_label.config(text="未找到匹配的课程/教学班")
                return
            for course, info, code in rows:
                if info["is_choose"] == "1":
                    state = "已选"
                elif info.get("is_conflict") == "1":
                    state = "时间冲突"
                elif not info.get("has_capacity_info"):
                    state = "仅查询"
                elif info["available"]:
                    state = "可选中"
                else:
                    state = "已满"
                iid = tree.insert("", tk.END, values=(
                    course.get("courseNumber", ""),
                    course.get("courseName", ""),
                    info["teacher"] or "教师待定",
                    info["course_index"] or info["teaching_class_id"],
                    info["place"],
                    f"{info['selected'] or '-'}/{info['capacity'] or '-'}",
                    state,
                ))
                rows_data[iid] = {"code": code, "id": info["teaching_class_id"], "info": info}
            status_label.config(text=f"共 {len(rows)} 个教学班；选中后点右下角按钮填入本课程")

        def do_search():
            keyword = keyword_entry.get().strip()
            course_type = type_values.get(type_box.get(), "major")
            search_button.config(state="disabled")
            status_label.config(text="查询中…")

            def worker():
                try:
                    courses = self.course_client.find_courses(keyword, course_type)
                    rows = []
                    for course in courses:
                        for tc in course.get("tcList") or []:
                            info = self.course_client.teaching_class_status(tc)
                            code = self.course_client.code_from_teaching_class_id(info["teaching_class_id"])
                            rows.append((course, info, code))
                    self.window.after(0, lambda: render(rows, None))
                except Exception as e:
                    self.window.after(0, lambda: render([], e))

            threading.Thread(target=worker, daemon=True).start()

        search_button.config(command=do_search)
        keyword_entry.bind("<Return>", lambda _event: do_search())
        dialog.after(100, do_search)

    def update_online_users(self):
        """更新在线人数"""
        try:
            online_users = self.course_client.get_person()
            self.window.title(f"选课系统 - {self.user_name} - 当前在线人数：{online_users}")
        except Exception as e:
            pass
        
        self.window.after(1000, self.update_online_users)

    def load_config(self):
        """读取配置文件，如果不存在则创建默认配置"""
        try:
            import yaml
            config_path = 'config.yaml'
            
            # 如果配置文件不存在，创建默认配置
            if not os.path.exists(config_path):
                default_config = {
                    'username': '',  # 默认空用户名
                    'password': '',  # 默认空密码
                    'courses': [
                        {'code': 'COMP30072701', 'type': 'major'},     # 计算机组成原理
                        {'code': 'CORE10010101', 'type': 'elective'},  # 大学英语
                        {'code': 'PHED10265003', 'type': 'physical'},  # 篮球
                        {'code': 'AUTO50112701', 'type': 'program'}    # 自动控制原理
                    ]
                }
                
                # 写入默认配置
                with open(config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(default_config, f, allow_unicode=True, sort_keys=False)
                
                # 提示用户
                messagebox.showinfo(
                    "提示", 
                    "已创建默认配置文件 config.yaml\n请在文件中填写你的账号信息"
                )
                
                return default_config
                
            # 读取现有配置
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
                
        except Exception as e:
            print(f"读取配置文件失败：{str(e)}")
            return {}

    def on_closing(self):
        """处理窗口关闭事件"""
        if messagebox.askokcancel("退出", "确定要退出程序吗？"):
            self.root.destroy()  # 完全退出程序

class LoginUI:
    def __init__(self):
        self.root = ttk.Window(themename="cosmo")
        self.root.title("西安交通大学选课系统")
        self.root.geometry("460x470")
        
        # 读取配置文件
        self.config = self.load_config()
        
        # 创建主框架
        self.main_frame = ttk.Frame(self.root, padding="20")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        title_label = ttk.Label(
            self.main_frame, 
            text="选课系统登录", 
            font=("Microsoft YaHei UI", 16, "bold")
        )
        title_label.pack(pady=20)
        
        username_frame = ttk.Frame(self.main_frame)
        username_frame.pack(fill=tk.X, pady=10)
        
        username_label = ttk.Label(
            username_frame, 
            text="学号：", 
            font=("Microsoft YaHei UI", 10)
        )
        username_label.pack(side=tk.LEFT)
        
        self.username_entry = ttk.Entry(username_frame, width=30)
        self.username_entry.pack(side=tk.LEFT, padx=5)
        self.username_entry.insert(0, self.config.get('username', ''))  # 从配置文件读取默认学号
        
        password_frame = ttk.Frame(self.main_frame)
        password_frame.pack(fill=tk.X, pady=10)
        
        password_label = ttk.Label(
            password_frame, 
            text="密码：", 
            font=("Microsoft YaHei UI", 10)
        )
        password_label.pack(side=tk.LEFT)
        
        self.password_entry = ttk.Entry(password_frame, width=30, show="*")
        self.password_entry.pack(side=tk.LEFT, padx=5)
        self.password_entry.insert(0, self.config.get('password', ''))
        
        self.login_button = ttk.Button(
            self.main_frame,
            text="登录",
            command=self.login,
            style="primary.TButton",
            width=32
        )
        self.login_button.pack(pady=(12, 4))

        self.browser_button = ttk.Button(
            self.main_frame,
            text="用浏览器登录（推荐 · 支持两步验证）",
            command=self.browser_login,
            style="success.TButton",
            width=32
        )
        self.browser_button.pack(pady=(0, 4))

        self.import_button = ttk.Button(
            self.main_frame,
            text="手动导入会话（高级 · 备用）",
            command=self.import_session,
            width=32
        )
        self.import_button.pack(pady=(0, 8))
        
        self.status_label = ttk.Label(
            self.main_frame,
            text="",
            font=("Microsoft YaHei UI", 9),
            wraplength=350
        )
        self.status_label.pack(pady=10)
        
    def login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        
        if not username or not password:
            messagebox.showerror("错误", "请输入学号和密码")
            return
            
        self.status_label.config(text="正在登录...")
        self.login_button.config(state="disabled")
        
        try:
            client = Login()
            success, message = client.login_process(username, password)
            
            if success:
                self._after_login_success(client, username)

            else:
                messagebox.showerror("登录失败", message)
                self.status_label.config(
                    text="登录失败",
                    foreground="red"
                )
                
        except Exception as e:
            messagebox.showerror("错误", f"发生错误：{str(e)}")
            self.status_label.config(
                text="登录失败",
                foreground="red"
            )
            
        finally:
            self.login_button.config(state="normal")
            
    def _after_login_success(self, client, username):
        """登录成功后的公共处理：创建选课客户端并打开主窗口。"""
        self.status_label.config(
            text=f"登录成功：{client.name}",
            foreground="green"
        )

        # 创建选课客户端
        course_client = CourseSelection(
            cookies=client.cookies,
            token=client.token,
            ticket=client.ticket,
            student_code=username or client.name
        )

        CourseSelectionUI(self.root, course_client, client.name)
        self.root.withdraw()

    def _set_login_buttons_state(self, state):
        """统一控制登录按钮的可用状态。"""
        for button in (self.login_button, self.browser_button, self.import_button):
            button.config(state=state)

    def browser_login(self):
        """一键登录：自动打开浏览器，用户在浏览器里完成登录（含两步验证）。"""
        username = self.username_entry.get().strip()
        if not username:
            messagebox.showerror("错误", "请在“学号”栏填写你的学号")
            return

        self._set_login_buttons_state("disabled")
        self.status_label.config(text="正在启动浏览器，请稍候…", foreground="black")

        result_queue = queue.Queue()

        def worker():
            try:
                client = Login()
                success, message = client.login_with_browser(
                    username=username,
                    on_status=lambda text: result_queue.put(("status", text, None)),
                )
            except Exception as e:
                success, message, client = False, f"发生错误：{str(e)}", None
            result_queue.put(("done", message, (success, client)))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(200, lambda: self._poll_login_queue(result_queue, username))

    def _poll_login_queue(self, result_queue, username):
        """在主线程中轮询后台登录结果（tkinter 不能跨线程更新界面）。"""
        try:
            while True:
                tag, text, extra = result_queue.get_nowait()
                if tag == "status":
                    self.status_label.config(text=text)
                    continue
                message = text
                success, client = extra
                break
        except queue.Empty:
            self.root.after(200, lambda: self._poll_login_queue(result_queue, username))
            return

        if success:
            self._after_login_success(client, username)
        else:
            self._set_login_buttons_state("normal")
            messagebox.showerror("登录失败", message)
            self.status_label.config(text="登录失败", foreground="red")

    def import_session(self):
        """浏览器已登录时导入 Cookie 直接建立会话（用于账号开启两步验证的情况）。"""
        dialog = ttk.Toplevel(self.root)
        dialog.title("手动导入会话（备用）")
        dialog.geometry("620x470")
        dialog.transient(self.root)
        dialog.grab_set()

        tip = (
            "备用方式：仅在“用浏览器登录”无法使用（如浏览器启动失败）时使用。\n\n"
            "1. 点击下方“打开选课系统”，在浏览器里登录（含两步验证）；\n"
            "2. 按 F12 → Console，输入 document.cookie 回车，复制输出的整段内容；\n"
            "   （也可在 Network 里任选一个请求，复制请求头中 Cookie: 后面的内容）\n"
            "3. 粘贴到下面文本框，确认“学号”已填写，点击“导入并登录”。\n\n"
            "提示：Cookie 等同于密码，请勿发给他人；会话过期后重新复制即可。"
        )
        ttk.Label(
            dialog,
            text=tip,
            justify=tk.LEFT,
            wraplength=580,
            font=("Microsoft YaHei UI", 9)
        ).pack(anchor=tk.W, padx=15, pady=(12, 6))

        text = tk.Text(dialog, height=9, wrap=tk.WORD, font=("Consolas", 9))
        text.pack(fill=tk.BOTH, expand=True, padx=15, pady=6)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=15, pady=(6, 12))

        ttk.Button(
            button_frame,
            text="打开选课系统",
            command=lambda: webbrowser.open(XKFW_INDEX_URL)
        ).pack(side=tk.LEFT)

        def do_import():
            cookie_text = text.get("1.0", tk.END).strip()
            if not cookie_text:
                messagebox.showwarning("提示", "请先粘贴 Cookie 内容", parent=dialog)
                return
            username = self.username_entry.get().strip()
            dialog.grab_release()
            dialog.destroy()
            self.login_button.config(state="disabled")
            self.import_button.config(state="disabled")
            self.status_label.config(text="正在导入会话...")
            try:
                client = Login()
                success, message = client.login_with_cookies(cookie_text, username)
                if success:
                    self._after_login_success(client, username)
                else:
                    messagebox.showerror("导入失败", message)
                    self.status_label.config(text="导入失败", foreground="red")
            except Exception as e:
                messagebox.showerror("错误", f"发生错误：{str(e)}")
                self.status_label.config(text="导入失败", foreground="red")
            finally:
                self.login_button.config(state="normal")
                self.import_button.config(state="normal")

        ttk.Button(
            button_frame,
            text="取消",
            command=dialog.destroy
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(
            button_frame,
            text="导入并登录",
            command=do_import,
            style="primary.TButton"
        ).pack(side=tk.RIGHT)

    def run(self):
        self.root.mainloop()

    def load_config(self):
        """读取配置文件，如果不存在则创建默认配置"""
        try:
            import yaml
            config_path = 'config.yaml'
            
            # 如果配置文件不存在，创建默认配置
            if not os.path.exists(config_path):
                default_config = {
                    'username': '',  # 默认空用户名
                    'password': '',  # 默认空密码
                    'courses': [
                        {'code': 'COMP30072701', 'type': 'major'},     # 计算机组成原理
                        {'code': 'CORE10010101', 'type': 'elective'},  # 大学英语
                        {'code': 'PHED10265003', 'type': 'physical'},  # 篮球
                        {'code': 'AUTO50112701', 'type': 'program'}    # 自动控制原理
                    ]
                }
                
                # 写入默认配置
                with open(config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(default_config, f, allow_unicode=True, sort_keys=False)
                
                # 提示用户
                messagebox.showinfo(
                    "提示", 
                    "已创建默认配置文件 config.yaml\n请在文件中填写你的账号信息"
                )
                
                return default_config
                
            # 读取现有配置
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
                
        except Exception as e:
            print(f"读取配置文件失败：{str(e)}")
            return {}

if __name__ == "__main__":
    app = LoginUI()
    app.run() 