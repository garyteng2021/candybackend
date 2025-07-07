
import os
import psycopg2
from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime
from dotenv import load_dotenv
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

app = Flask(__name__)
CORS(app, supports_credentials=True)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:GmZZmzBLdzmimTqSmcsdSARPPWBOfKqS@postgres.railway.internal:5432/railway")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_tables():
    with get_conn() as conn:
        with conn.cursor() as c:
            # 创建 users 表
            c.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    phone TEXT,
                    points INTEGER DEFAULT 0,
                    plays INTEGER DEFAULT 0,
                    inviter TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_game_time TIMESTAMP,
                    blocked BOOLEAN DEFAULT FALSE
                );
            """)
            
            # 确保 token 字段存在
            c.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='users' AND column_name='token') THEN
                        ALTER TABLE users ADD COLUMN token TEXT;
                    END IF;
                END
                $$;
            """)
            
            # 创建 game_logs 表
            c.execute("""
                CREATE TABLE IF NOT EXISTS game_logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    user_roll INTEGER,
                    bot_roll INTEGER,
                    result TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            conn.commit()
    print("✅ 数据表初始化完成")

def daily_token_update():
    with get_conn() as conn:
        with conn.cursor() as c:
            # 每天+2，最大不超过20
            c.execute("UPDATE users SET token = LEAST(COALESCE(token,0) + 2, 20)")
            conn.commit()
    print("每日token补充完成")

# 启动定时任务
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")  # 可调整为你服务器时区
scheduler.add_job(daily_token_update, 'cron', hour=0, minute=0)  # 每天00:00自动执行
scheduler.start()
    
@app.route("/admin")
def admin():
    keyword = request.args.get("q", "")
    filter_blocked = request.args.get("filter")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    # 分页参数
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))

    # 拼装WHERE
    where_clauses = ["TRUE"]
    params = []
    if keyword:
        where_clauses.append("(username ILIKE %s OR phone ILIKE %s OR inviter ILIKE %s)")
        params += [f"%{keyword}%"] * 3
    if filter_blocked == "1":
        where_clauses.append("blocked = TRUE")
    elif filter_blocked == "0":
        where_clauses.append("blocked IS FALSE")
    if start_date:
        where_clauses.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        where_clauses.append("created_at <= %s")
        params.append(end_date)
    where = " AND ".join(where_clauses)

    # 查询总数
    count_query = f"SELECT COUNT(*) FROM users WHERE {where}"
    user_query = f"SELECT * FROM users WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s"

    with get_conn() as conn:
        with conn.cursor() as c:
            # 获取总用户数
            c.execute(count_query, params)
            total = c.fetchone()[0]
            total_pages = (total + page_size - 1) // page_size if total else 1
            # 分页查询用户
            c.execute(user_query, params + [page_size, (page-1)*page_size])
            rows = c.fetchall()
            users = [dict(zip([desc[0] for desc in c.description], row)) for row in rows]
            # 查询被邀请人数和当日最高分
            for user in users:
                c.execute("SELECT COUNT(*) FROM users WHERE inviter = %s", (str(user["user_id"]),))
                user["invited_count"] = c.fetchone()[0]
                c.execute("""
                    SELECT MAX(user_roll)
                    FROM game_logs
                    WHERE user_id = %s AND timestamp::date = CURRENT_DATE
                """, (user["user_id"],))
                user["daily_max_score"] = c.fetchone()[0] or 0

            # 数据统计
            c.execute("SELECT COUNT(*) FROM users")
            total_all = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users WHERE phone IS NOT NULL")
            verified = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users WHERE blocked = TRUE")
            blocked = c.fetchone()[0]
            c.execute("SELECT SUM(points) FROM users")
            points = c.fetchone()[0] or 0

    stats = {"total": total_all, "verified": verified, "blocked": blocked, "points": points}

    # 拼接保留的搜索参数用于分页跳转
    qstr = ""
    for k in ["q", "start_date", "end_date", "filter"]:
        v = request.args.get(k, "")
        if v:
            qstr += f"&{k}={v}"

    return render_template(
        "admin.html",
        users=users,
        stats=stats,
        request=request,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        qstr=qstr,
        keyword=keyword,
    )
    
@app.route("/api/user_info", methods=["GET"])
def user_info():
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "缺少 user_id"}), 400
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT username, phone, points, COALESCE(token, '0'), COALESCE(plays, 0) FROM users WHERE user_id = %s", (user_id,))
            row = c.fetchone()
            if row:
                return jsonify({
                    "status": "ok",
                    "user_id": user_id,
                    "username": row[0],
                    "phone": row[1],
                    "points": row[2] or 0,
                    "token": int(row[3]) if row[3] else 0,
                    "plays": row[4] or 0,
                })
            else:
                return jsonify({"status": "not_found"}), 404

@app.route("/user/logs")
def user_logs():
    user_id = request.args.get("user_id")
    page = int(request.args.get("page", 1))
    page_size = 20
    with get_conn() as conn:
        with conn.cursor() as c:
            # 统计总条数
            c.execute("SELECT COUNT(*) FROM game_logs WHERE user_id = %s", (user_id,))
            total = c.fetchone()[0]
            total_pages = (total + page_size - 1) // page_size if total else 1
            # 查询本页
            c.execute("SELECT * FROM game_logs WHERE user_id = %s ORDER BY timestamp DESC LIMIT %s OFFSET %s",
                      (user_id, page_size, (page-1)*page_size))
            logs = [dict(zip([desc[0] for desc in c.description], row)) for row in c.fetchall()]
    return render_template("user_logs.html",
                           logs=logs,
                           user_id=user_id,
                           page=page,
                           total_pages=total_pages)

@app.route("/invitees")
def invitees():
    user_id = request.args.get("user_id")
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT * FROM users WHERE inviter = %s", (user_id,))
            invitees = [dict(zip([desc[0] for desc in c.description], row)) for row in c.fetchall()]
    return render_template("invitees.html", invitees=invitees)

@app.route("/admin/rank/today")
def rank_today():
    from datetime import datetime
    # 获取前端选定日期（默认为今天）
    date_str = request.args.get("date")
    if not date_str:
        date = datetime.now().date()
        date_str = date.strftime("%Y-%m-%d")
    else:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()

    with get_conn() as conn:
        with conn.cursor() as c:
            # 查该日每用户积分总和并关联用户基本信息
            c.execute("""
                SELECT
                    u.user_id,
                    u.username,
                    u.phone,
                    u.created_at,
                    u.last_game_time,
                    COALESCE(SUM(g.user_roll), 0) as day_points
                FROM users u
                LEFT JOIN game_logs g ON u.user_id = g.user_id AND g.timestamp::date = %s
                GROUP BY u.user_id, u.username, u.phone, u.created_at, u.last_game_time
                ORDER BY day_points DESC
                LIMIT 20
            """, (date_str,))
            users = [
                {
                    "user_id": row[0],
                    "username": row[1],
                    "phone": row[2],
                    "created_at": row[3],
                    "last_game_time": row[4],
                    "points": row[5]
                }
                for row in c.fetchall()
            ]

    return render_template("rank_today.html", users=users, date=date_str)

@app.route("/user/save", methods=["POST"])
def save_user():
    data = request.get_json()
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE users SET blocked = %s, points = %s, plays = %s, token = %s
                WHERE user_id = %s
            """, (
                data.get("blocked", False),
                data.get("points", 0),
                data.get("plays", 0),
                data.get("token", 0),        # 新增！保存token
                data["user_id"]
            ))
            conn.commit()
    return jsonify({"status": "ok"})

@app.route("/user/delete", methods=["POST"])
def delete_user():
    data = request.get_json()
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("DELETE FROM users WHERE user_id = %s", (data["user_id"],))
            conn.commit()
    return jsonify({"status": "ok"})

@app.route("/")
def index():
    return redirect(url_for("admin"))
    
@app.route("/game")
def game():
    return render_template("game.html")

@app.route("/play", methods=["POST"])
def play_game():
    try:
        # 打印收到的参数，方便排查
        print("收到参数:", dict(request.form))

        user_id = request.form.get("user_id")
        score_str = request.form.get("score", "0")
        game_name = request.form.get("game_name", "骰子对赌")

        if not user_id:
            return jsonify({"error": "缺少 user_id"}), 400

        try:
            score = int(score_str)
        except Exception as e:
            return jsonify({"error": f"score字段格式错误，收到: {score_str}"}), 400

        with get_conn() as conn:
            with conn.cursor() as c:
                # 查询用户，打印 token 字段内容
                c.execute("SELECT points, plays, COALESCE(token::int, 0) FROM users WHERE user_id = %s", (user_id,))
                result = c.fetchone()
                print("用户数据: ", result)
                if not result:
                    return jsonify({"error": "用户不存在"}), 404

                old_points, old_plays, old_token = result
                new_points = (old_points or 0) + score
                new_plays = (old_plays or 0) + 1
                new_token = max((old_token or 0) - 1, 0)

                # 更新用户积分、次数、token、时间
                c.execute("UPDATE users SET points = %s, plays = %s, last_game_time = NOW(), token = %s WHERE user_id = %s",
                          (new_points, new_plays, new_token, user_id))

                c.execute("""
                    INSERT INTO game_logs (user_id, user_roll, bot_roll, result, timestamp, game_name)
                    VALUES (%s, %s, %s, %s, NOW(), %s)
                """, (user_id, score, 0, '游戏结束', game_name))

                conn.commit()

                c.execute("SELECT username, phone, points, COALESCE(token::int, 0) FROM users WHERE user_id = %s", (user_id,))
                user = c.fetchone()
                data = {
                    "username": user[0],
                    "phone": user[1],
                    "points": user[2],
                    "score": score,
                    "result": "提交成功",
                    "token": user[3]
                }
        return jsonify(data)

    except Exception as e:
        import traceback
        print("❌ /play接口报错:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/rank")
def api_rank():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT user_id, username, phone, points
                FROM users
                ORDER BY points DESC
                LIMIT 10
            """)
            results = [
                {
                    "user_id": row[0],
                    "username": row[1],
                    "phone": row[2],
                    "points": row[3]
                }
                for row in c.fetchall()
            ]
    return jsonify(results)

@app.route("/user/bind", methods=["POST"])
def user_bind():
    data = request.get_json()
    user_id = data.get("user_id")
    phone = data.get("phone")
    username = data.get("username", "")
    inviter = data.get("inviter")  # 新增参数

    if not user_id or not phone:
        return jsonify({"status": "error", "message": "参数不全"}), 400

    with get_conn() as conn:
        with conn.cursor() as c:
            # 插入新用户，token=5，写入inviter
            c.execute("""
                INSERT INTO users (user_id, phone, username, inviter, token)
                VALUES (%s, %s, %s, %s, 5)
                ON CONFLICT (user_id) DO UPDATE SET phone=EXCLUDED.phone, username=EXCLUDED.username
            """, (user_id, phone, username, inviter))
            # 如果有邀请人，奖励邀请人+3 token
            if inviter:
                c.execute("UPDATE users SET token = COALESCE(token,0) + 3 WHERE user_id = %s", (inviter,))
            conn.commit()
    return jsonify({"status": "ok"})

@app.route("/api/check_bind", methods=["GET"])
def check_bind():
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "缺少 user_id"}), 400
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT phone FROM users WHERE user_id = %s", (user_id,))
            row = c.fetchone()
            if row and row[0]:
                return jsonify({"status": "ok"})
            else:
                return jsonify({"status": "not_bind"}), 401


@app.template_filter('format_datetime')
def format_datetime(value):
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")

@app.route("/api/profile", methods=["GET"])
def profile():
    return user_info()

@app.route("/init")
def run_init_tables():
    try:
        init_tables()
        return "✅ 数据表已初始化/更新", 200
    except Exception as e:
        return f"❌ 初始化失败: {e}", 500

if __name__ == "__main__":
    init_tables()  # 自动建表 ✅
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)

