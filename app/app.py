from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)


@app.after_request
def set_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


@app.route("/")
def index():
    name = request.args.get("name", "Nikita")

    html = """
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <title>Secure App Delivery Lab</title>
    </head>
    <body>
        <h1>Secure App Delivery Lab</h1>
        <p>Тестовое веб-приложение для проверки CI/CD, Kubernetes и OWASP ZAP.</p>

        <form method="get">
            <label>Введите имя:</label>
            <input type="text" name="name">
            <button type="submit">Отправить</button>
        </form>

        <p>Привет, {{ name }}</p>
    </body>
    </html>
    """

    return render_template_string(html, name=name)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)