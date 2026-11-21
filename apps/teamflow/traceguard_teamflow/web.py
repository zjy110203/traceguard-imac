"""TeamFlow Web 页面（服务端渲染 HTML + 原生 JS，Playwright 可直接测试）。

页面不依赖构建工具，规避本沙箱 node 构建限制；使用 data-testid 提供稳定定位。
页面内容是"不可信数据"，不授予任何工具权限（安全基线）。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .faults import FAULT_STATE

router = APIRouter()


_BASE_CSS = """
<style>
  :root{color-scheme:light} body{font-family:system-ui,sans-serif;color:#1f2937;max-width:900px;margin:2rem auto;padding:0 1rem}
  h1{font-size:1.6rem} .card{border:1px solid #e5e7eb;border-radius:10px;padding:1.2rem;margin:1rem 0}
  label{display:block;margin:.6rem 0 .2rem;font-weight:600} input,textarea{padding:.5rem;border:1px solid #d1d5db;border-radius:6px;width:100%;box-sizing:border-box}
  button{background:#3730a3;color:#fff;border:none;border-radius:6px;padding:.6rem 1.2rem;cursor:pointer;margin-top:.8rem}
  .success{color:#15803d;background:#dcfce7;padding:.6rem;border-radius:6px} .error{color:#b91c1c;background:#fee2e2;padding:.6rem;border-radius:6px}
  table{border-collapse:collapse;width:100%} th,td{border:1px solid #e5e7eb;padding:.5rem;text-align:left} a{color:#3730a3}
  nav a{margin-right:1rem}
</style>
"""


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} - TeamFlow</title>{_BASE_CSS}</head>
<body>{body}</body></html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> str:
    body = """
    <h1>TeamFlow 登录</h1>
    <div class="card">
      <form id="login-form" data-testid="login-form">
        <label for="username">用户名</label>
        <input id="username" data-testid="username" name="username" value="tester" autocomplete="off">
        <label for="password">密码</label>
        <input id="password" data-testid="password" name="password" type="password" value="tester123" autocomplete="off">
        <button type="submit" data-testid="login-submit">登录</button>
      </form>
      <div id="msg" data-testid="login-message"></div>
    </div>
    <script>
      document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const u = document.getElementById('username').value;
        const p = document.getElementById('password').value;
        const res = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:u,password:p})});
        const msg = document.getElementById('msg');
        if (res.ok) { msg.className='success'; msg.textContent='登录成功'; sessionStorage.setItem('tf_token','ok'); setTimeout(()=>location.href='/projects',400); }
        else { msg.className='error'; msg.textContent='登录失败：用户名或密码错误'; }
      });
    </script>
    """
    return _page("登录", body)


@router.get("/projects", response_class=HTMLResponse)
async def projects_page() -> str:
    ui_fault = FAULT_STATE.is_active("TF-UI-001")
    body = f"""
    <nav><a href="/projects" data-testid="nav-projects">项目</a><a href="/projects/new" data-testid="nav-new">新建项目</a></nav>
    <h1 data-testid="page-title">项目列表</h1>
    <div id="banner" data-testid="banner"></div>
    <div class="card">
      <table><thead><tr><th>Key</th><th>名称</th><th>状态</th><th>操作</th></tr></thead>
      <tbody id="rows" data-testid="project-rows"></tbody></table>
      <p id="empty" data-testid="empty-state" style="display:none">暂无项目</p>
    </div>
    <script>
      const uiFault = {"true" if ui_fault else "false"};
      async function load() {{
        const res = await fetch('/api/projects?workspace=default');
        const data = await res.json();
        const rows = document.getElementById('rows');
        rows.innerHTML='';
        if (data.items.length===0) {{ document.getElementById('empty').style.display='block'; }}
        else {{ document.getElementById('empty').style.display='none';
          data.items.forEach(p => {{
            const tr=document.createElement('tr');
            tr.setAttribute('data-testid','project-row');
            tr.innerHTML=`<td>${{p.key}}</td><td>${{p.name}}</td><td>${{p.status}}</td><td><a data-testid="detail-link" href="/projects/${{p.key}}">详情</a></td>`;
            rows.appendChild(tr);
          }});
        }}
      }}
      const banner = document.getElementById('banner');
      const params = new URLSearchParams(location.search);
      if (params.get('created')==='1') {{
        if (uiFault) {{ banner.className='error'; banner.setAttribute('data-testid','banner-error'); banner.textContent='创建失败（UI 表现异常）'; }}
        else {{ banner.className='success'; banner.setAttribute('data-testid','banner-success'); banner.textContent='项目创建成功'; }}
      }}
      load();
    </script>
    """
    return _page("项目列表", body)


@router.get("/projects/new", response_class=HTMLResponse)
async def new_project_page() -> str:
    body = """
    <nav><a href="/projects" data-testid="nav-projects">项目</a></nav>
    <h1 data-testid="page-title">新建项目</h1>
    <div class="card">
      <form id="create-form" data-testid="create-form">
        <label for="name">项目名称</label>
        <input id="name" data-testid="project-name" name="name" required>
        <label for="description">描述</label>
        <textarea id="description" data-testid="project-description" name="description"></textarea>
        <button type="submit" data-testid="create-submit">创建</button>
      </form>
      <div id="msg" data-testid="create-message"></div>
    </div>
    <script>
      document.getElementById('create-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('name').value;
        const description = document.getElementById('description').value;
        const res = await fetch('/api/projects', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, description, workspace_slug:'default'})});
        const msg = document.getElementById('msg');
        if (res.ok) { const d = await res.json(); location.href='/projects?created=1'; }
        else { const d = await res.json().catch(()=>({})); msg.className='error'; msg.setAttribute('data-testid','create-error'); msg.textContent='创建失败：'+(d.detail||res.status); }
      });
    </script>
    """
    return _page("新建项目", body)


@router.get("/projects/{project_key}", response_class=HTMLResponse)
async def project_detail_page(project_key: str) -> str:
    body = f"""
    <nav><a href="/projects" data-testid="nav-projects">项目</a></nav>
    <h1 data-testid="page-title">项目详情</h1>
    <div class="card" id="detail" data-testid="project-detail">
      <p data-testid="detail-key">Key: {project_key}</p>
      <p id="detail-name" data-testid="detail-name">加载中…</p>
      <p id="detail-status" data-testid="detail-status"></p>
    </div>
    <script>
      (async () => {{
        const res = await fetch('/api/projects/{project_key}');
        if (res.ok) {{ const d = await res.json();
          document.getElementById('detail-name').textContent='名称: '+d.project.name;
          document.getElementById('detail-status').textContent='状态: '+d.project.status;
        }} else {{
          document.getElementById('detail-name').textContent='项目不存在';
        }}
      }})();
    </script>
    """
    return _page("项目详情", body)
