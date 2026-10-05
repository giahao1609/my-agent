from core.code_graph import CodeEdgeKind, CodeNodeKind
from core.polyglot_code_scanner import scan_polyglot_project


def _make_laravel_project(tmp_path):
    (tmp_path / "composer.json").write_text(
        '{"require":{"laravel/framework":"^13.0"}}',
        encoding="utf-8",
    )
    routes = tmp_path / "routes" / "api"
    routes.mkdir(parents=True)
    return routes


def test_partial_laravel_route_resolves_from_seed_symbols(tmp_path):
    routes = _make_laravel_project(tmp_path)
    route_file = routes / "auth.php"
    route_file.write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "Route::post('/login', [AuthController::class, 'login']);\n",
        encoding="utf-8",
    )

    seed_id = "persisted-auth-login"
    result = scan_polyglot_project(
        "demo",
        tmp_path,
        include_paths={"routes/api/auth.php"},
        seed_symbols={
            "App\\Http\\Controllers\\AuthController::login": seed_id,
        },
    )

    route_edges = [e for e in result.edges if e.kind is CodeEdgeKind.ROUTES_TO]
    assert len(route_edges) == 1
    assert route_edges[0].target_id == seed_id
    assert route_edges[0].metadata.get("resolved") is True
    assert not any(
        n.kind is CodeNodeKind.EXTERNAL
        and n.metadata.get("role") == "route_target"
        for n in result.nodes
    )


def test_polyglot_scan_prunes_vendor_and_node_modules(tmp_path):
    (tmp_path / "app.py").write_text("def visible():\n    return 1\n", encoding="utf-8")

    vendor = tmp_path / "vendor" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "hidden.py").write_text("def hidden():\n    return 1\n", encoding="utf-8")

    node_modules = tmp_path / "node_modules" / "pkg"
    node_modules.mkdir(parents=True)
    (node_modules / "hidden.py").write_text("def hidden2():\n    return 1\n", encoding="utf-8")

    result = scan_polyglot_project("demo", tmp_path)
    file_paths = {n.path for n in result.nodes if n.kind is CodeNodeKind.FILE}

    assert "app.py" in file_paths
    assert "vendor/pkg/hidden.py" not in file_paths
    assert "node_modules/pkg/hidden.py" not in file_paths


def test_go_calls_resolve_local_targets_and_skip_predeclared(tmp_path):
    go_file = tmp_path / "service.go"
    go_file.write_text(
        "package demo\n"
        "import \"fmt\"\n"
        "type Service struct{}\n"
        "func helper() string { return \"ok\" }\n"
        "func (s *Service) now() string { return \"now\" }\n"
        "func (s *Service) Run() string {\n"
        "    _ = len(\"abc\")\n"
        "    _ = fmt.Sprintf(\"%s\", helper())\n"
        "    return s.now()\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)
    by_id = {n.node_id: n for n in result.nodes}
    calls = [e for e in result.edges if e.kind is CodeEdgeKind.CALLS]

    pairs = {
        (
            by_id[e.source_id].qualified_name,
            by_id[e.target_id].qualified_name or by_id[e.target_id].name,
            e.metadata.get("resolved"),
            e.metadata.get("go_call_kind"),
        )
        for e in calls
        if e.source_id in by_id and e.target_id in by_id
    }

    assert any(
        src.endswith("::Service::Run")
        and dst.endswith("::helper")
        and resolved is True
        and kind == "package_function"
        for src, dst, resolved, kind in pairs
    )
    assert any(
        src.endswith("::Service::Run")
        and dst.endswith("::Service::now")
        and resolved is True
        and kind == "receiver_method"
        for src, dst, resolved, kind in pairs
    )
    assert any(
        src.endswith("::Service::Run")
        and dst == "fmt.Sprintf"
        and resolved is False
        and kind == "import_function"
        for src, dst, resolved, kind in pairs
    )
    assert not any(
        n.kind is CodeNodeKind.EXTERNAL
        and n.language == "go"
        and n.name == "len"
        for n in result.nodes
    )


def test_go_implements_matches_signature_and_receiver_form(tmp_path):
    (tmp_path / "demo.go").write_text(
        "package demo\n"
        "type Runner interface { Run(x string) error }\n"
        "type ValueRunner struct{}\n"
        "func (ValueRunner) Run(x string) error { return nil }\n"
        "type PointerRunner struct{}\n"
        "func (*PointerRunner) Run(x string) error { return nil }\n"
        "type WrongRunner struct{}\n"
        "func (*WrongRunner) Run(x int) error { return nil }\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)
    by_id = {n.node_id: n for n in result.nodes}
    implements = [
        e for e in result.edges
        if e.kind is CodeEdgeKind.IMPLEMENTS
    ]
    pairs = {
        (
            by_id[e.source_id].qualified_name,
            by_id[e.target_id].qualified_name,
            e.metadata.get("receiver_form"),
        )
        for e in implements
    }

    assert ("demo::ValueRunner", "demo::Runner", "value") in pairs
    assert ("demo::PointerRunner", "demo::Runner", "pointer") in pairs
    assert not any(src == "demo::WrongRunner" for src, _, _ in pairs)


def test_java_semantic_v1_inherits_implements_and_local_calls(tmp_path):
    (tmp_path / "Service.java").write_text(
        "package demo.service;\n"
        "interface Runner { String run(int x); }\n"
        "class Base {}\n"
        "class Service extends Base implements Runner {\n"
        "  public String run(int x) { return helper(x); }\n"
        "  private String helper(int x) { return String.valueOf(x); }\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)
    by_id = {n.node_id: n for n in result.nodes}

    inherits = [e for e in result.edges if e.kind is CodeEdgeKind.INHERITS]
    implements = [e for e in result.edges if e.kind is CodeEdgeKind.IMPLEMENTS]
    calls = [e for e in result.edges if e.kind is CodeEdgeKind.CALLS]

    assert any(
        by_id[e.source_id].qualified_name == "demo.service.Service"
        and by_id[e.target_id].qualified_name == "demo.service.Base"
        for e in inherits
    )
    assert any(
        by_id[e.source_id].qualified_name == "demo.service.Service"
        and by_id[e.target_id].qualified_name == "demo.service.Runner"
        for e in implements
    )
    assert any(
        by_id[e.source_id].qualified_name == "demo.service.Service::run(int)->String"
        and by_id[e.target_id].qualified_name == "demo.service.Service::helper(int)->String"
        and e.metadata.get("resolved") is True
        and e.metadata.get("java_call_kind") == "local_method"
        for e in calls
    )
    helper = next(
        n for n in result.nodes
        if n.qualified_name == "demo.service.Service::helper(int)->String"
    )
    assert helper.metadata.get("java_signature") == "(int)->String"


def test_js_ts_cross_file_named_import_resolves(tmp_path):
    (tmp_path / "service.ts").write_text("export function createOrder(id: string): string {\n  return id;\n}\n", encoding="utf-8")
    (tmp_path / "controller.ts").write_text("import { createOrder } from \"./service\";\nexport function handle(id: string): string {\n  return createOrder(id);\n}\n", encoding="utf-8")
    result = scan_polyglot_project("demo", tmp_path)
    handle = next(n for n in result.nodes if n.path == "controller.ts" and n.name == "handle")
    create_order = next(n for n in result.nodes if n.path == "service.ts" and n.name == "createOrder")
    controller_file = next(n for n in result.nodes if n.path == "controller.ts" and n.kind is CodeNodeKind.FILE)
    service_file = next(n for n in result.nodes if n.path == "service.ts" and n.kind is CodeNodeKind.FILE)
    assert any(e.kind is CodeEdgeKind.CALLS and e.source_id == handle.node_id and e.target_id == create_order.node_id and e.metadata.get("resolved") is True for e in result.edges)
    assert any(e.kind is CodeEdgeKind.IMPORTS and e.source_id == controller_file.node_id and e.target_id == service_file.node_id and e.metadata.get("resolved") is True for e in result.edges)


def test_js_ts_cross_file_namespace_call_resolves(tmp_path):
    (tmp_path / "service.ts").write_text("export function createOrder(): number {\n  return 1;\n}\n", encoding="utf-8")
    (tmp_path / "controller.ts").write_text("import * as service from \"./service\";\nexport function handle(): number {\n  return service.createOrder();\n}\n", encoding="utf-8")
    result = scan_polyglot_project("demo", tmp_path)
    handle = next(n for n in result.nodes if n.path == "controller.ts" and n.name == "handle")
    target = next(n for n in result.nodes if n.path == "service.ts" and n.name == "createOrder")
    assert any(e.kind is CodeEdgeKind.CALLS and e.source_id == handle.node_id and e.target_id == target.node_id and e.metadata.get("resolved") is True for e in result.edges)


def test_js_ts_cross_file_inheritance_resolves(tmp_path):
    (tmp_path / "base.ts").write_text("export class BaseController {}\n", encoding="utf-8")
    (tmp_path / "controller.ts").write_text("import { BaseController } from \"./base\";\nexport class OrderController extends BaseController {}\n", encoding="utf-8")
    result = scan_polyglot_project("demo", tmp_path)
    child = next(n for n in result.nodes if n.path == "controller.ts" and n.name == "OrderController")
    parent = next(n for n in result.nodes if n.path == "base.ts" and n.name == "BaseController")
    assert any(e.kind is CodeEdgeKind.INHERITS and e.source_id == child.node_id and e.target_id == parent.node_id and e.metadata.get("resolved") is True for e in result.edges)


def test_js_ts_incremental_scan_resolves_from_seed_symbols(tmp_path):
    (tmp_path / "service.ts").write_text("export function createOrder(): number {\n  return 1;\n}\n", encoding="utf-8")
    (tmp_path / "controller.ts").write_text("import { createOrder } from \"./service\";\nexport function handle(): number {\n  return createOrder();\n}\n", encoding="utf-8")
    full = scan_polyglot_project("demo", tmp_path)
    target = next(n for n in full.nodes if n.path == "service.ts" and n.name == "createOrder")
    seed_symbols = {n.qualified_name: n.node_id for n in full.nodes if n.qualified_name}
    partial = scan_polyglot_project("demo", tmp_path, include_paths={"controller.ts"}, seed_symbols=seed_symbols)
    handle = next(n for n in partial.nodes if n.path == "controller.ts" and n.name == "handle")
    assert any(e.kind is CodeEdgeKind.CALLS and e.source_id == handle.node_id and e.target_id == target.node_id and e.metadata.get("resolved") is True for e in partial.edges)


def test_php_cross_file_inheritance_resolves(tmp_path):
    (tmp_path / "BaseService.php").write_text("<?php\nclass BaseService {}\n", encoding="utf-8")
    (tmp_path / "OrderService.php").write_text("<?php\nclass OrderService extends BaseService {}\n", encoding="utf-8")
    result = scan_polyglot_project("demo", tmp_path)
    child = next(n for n in result.nodes if n.path == "OrderService.php" and n.name == "OrderService")
    parent = next(n for n in result.nodes if n.path == "BaseService.php" and n.name == "BaseService")
    assert any(e.kind is CodeEdgeKind.INHERITS and e.source_id == child.node_id and e.target_id == parent.node_id for e in result.edges)


def test_php_cross_file_inheritance_use_alias_resolves(tmp_path):
    (tmp_path / "BaseService.php").write_text("<?php\nnamespace App\\Core;\nclass BaseService {}\n", encoding="utf-8")
    (tmp_path / "OrderService.php").write_text("<?php\nnamespace App\\Orders;\nuse App\\Core\\BaseService as Parent;\nclass OrderService extends Parent {}\n", encoding="utf-8")
    result = scan_polyglot_project("demo", tmp_path)
    child = next(n for n in result.nodes if n.qualified_name == "App\\Orders\\OrderService")
    parent = next(n for n in result.nodes if n.qualified_name == "App\\Core\\BaseService")
    assert any(e.kind is CodeEdgeKind.INHERITS and e.source_id == child.node_id and e.target_id == parent.node_id for e in result.edges)

def test_php_cross_file_static_method_call_resolves(tmp_path):
    (tmp_path / "OrderService.php").write_text(
        "<?php\nnamespace App\\Services;\nclass OrderService { public static function create() { return 1; } }\n",
        encoding="utf-8",
    )
    (tmp_path / "Controller.php").write_text(
        "<?php\nnamespace App\\Http;\nuse App\\Services\\OrderService;\nclass Controller { public function handle() { return OrderService::create(); } }\n",
        encoding="utf-8",
    )
    result = scan_polyglot_project("demo", tmp_path)
    source = next(n for n in result.nodes if n.qualified_name == "App\\Http\\Controller::handle")
    target = next(n for n in result.nodes if n.qualified_name == "App\\Services\\OrderService::create")
    assert any(
        e.kind is CodeEdgeKind.CALLS
        and e.source_id == source.node_id
        and e.target_id == target.node_id
        for e in result.edges
    )

def test_php_incremental_static_call_resolves_from_seed_symbols(tmp_path):
    (tmp_path / "OrderService.php").write_text(
        "<?php\nnamespace App\\Services;\nclass OrderService { public static function create() { return 1; } }\n",
        encoding="utf-8",
    )
    (tmp_path / "Controller.php").write_text(
        "<?php\nnamespace App\\Http;\nuse App\\Services\\OrderService;\nclass Controller { public function handle() { return OrderService::create(); } }\n",
        encoding="utf-8",
    )
    full = scan_polyglot_project("demo", tmp_path)
    target = next(n for n in full.nodes if n.qualified_name == "App\\Services\\OrderService::create")
    seed_symbols = {n.qualified_name: n.node_id for n in full.nodes if n.qualified_name}
    partial = scan_polyglot_project(
        "demo",
        tmp_path,
        include_paths={"Controller.php"},
        seed_symbols=seed_symbols,
    )
    source = next(n for n in partial.nodes if n.qualified_name == "App\\Http\\Controller::handle")
    assert any(
        e.kind is CodeEdgeKind.CALLS
        and e.source_id == source.node_id
        and e.target_id == target.node_id
        for e in partial.edges
    )

def test_php_cross_file_typed_instance_method_call_resolves(tmp_path):
    (tmp_path / "OrderService.php").write_text(
        "<?php\nnamespace App\\Services;\nclass OrderService { public function create() { return 1; } }\n",
        encoding="utf-8",
    )
    (tmp_path / "Controller.php").write_text(
        "<?php\nnamespace App\\Http;\nuse App\\Services\\OrderService;\nclass Controller { public function handle(OrderService $service) { return $service->create(); } }\n",
        encoding="utf-8",
    )
    result = scan_polyglot_project("demo", tmp_path)
    source = next(n for n in result.nodes if n.qualified_name == "App\\Http\\Controller::handle")
    target = next(n for n in result.nodes if n.qualified_name == "App\\Services\\OrderService::create")
    assert any(
        e.kind is CodeEdgeKind.CALLS
        and e.source_id == source.node_id
        and e.target_id == target.node_id
        for e in result.edges
    )

def test_php_cross_file_interface_implements_resolves(tmp_path):
    (tmp_path / "OrderWriter.php").write_text(
        "<?php\nnamespace App\\Contracts;\ninterface OrderWriter {}\n",
        encoding="utf-8",
    )
    (tmp_path / "OrderService.php").write_text(
        "<?php\nnamespace App\\Services;\nuse App\\Contracts\\OrderWriter;\nclass OrderService implements OrderWriter {}\n",
        encoding="utf-8",
    )
    result = scan_polyglot_project("demo", tmp_path)
    source = next(n for n in result.nodes if n.qualified_name == "App\\Services\\OrderService")
    target = next(n for n in result.nodes if n.qualified_name == "App\\Contracts\\OrderWriter")
    assert any(
        e.kind is CodeEdgeKind.IMPLEMENTS
        and e.source_id == source.node_id
        and e.target_id == target.node_id
        for e in result.edges
    )

def test_next_app_router_api_routes_resolve_to_handlers(tmp_path):
    app = tmp_path / "apps" / "web"
    route_dir = app / "src" / "app" / "api" / "auth" / "login"
    route_dir.mkdir(parents=True)
    (app / "package.json").write_text(
        '{"dependencies":{"next":"14.2.35","react":"18.3.1"}}',
        encoding="utf-8",
    )
    (route_dir / "route.ts").write_text(
        "export async function GET() { return Response.json({ ok: true }); }\n"
        "export async function POST() { return Response.json({ ok: true }); }\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    get_handler = next(n for n in result.nodes if n.path == "apps/web/src/app/api/auth/login/route.ts" and n.name == "GET")
    post_handler = next(n for n in result.nodes if n.path == "apps/web/src/app/api/auth/login/route.ts" and n.name == "POST")
    get_route = next(n for n in result.nodes if n.kind is CodeNodeKind.ROUTE and n.qualified_name == "GET /api/auth/login")
    post_route = next(n for n in result.nodes if n.kind is CodeNodeKind.ROUTE and n.qualified_name == "POST /api/auth/login")

    assert any(e.kind is CodeEdgeKind.ROUTES_TO and e.source_id == get_route.node_id and e.target_id == get_handler.node_id for e in result.edges)
    assert any(e.kind is CodeEdgeKind.ROUTES_TO and e.source_id == post_route.node_id and e.target_id == post_handler.node_id for e in result.edges)


def test_react_tsx_components_are_classified(tmp_path):
    (tmp_path / "page.tsx").write_text(
        "export default function Page() {\n"
        "  return <main>Hello</main>;\n"
        "}\n"
        "\n"
        "export const Header = () => <header>Header</header>;\n"
        "\n"
        "export function helper(): number {\n"
        "  return 1;\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    page = next(n for n in result.nodes if n.path == "page.tsx" and n.name == "Page")
    header = next(n for n in result.nodes if n.path == "page.tsx" and n.name == "Header")
    helper = next(n for n in result.nodes if n.path == "page.tsx" and n.name == "helper")

    assert page.kind is CodeNodeKind.COMPONENT
    assert header.kind is CodeNodeKind.COMPONENT
    assert helper.kind is CodeNodeKind.FUNCTION


def test_next_app_router_page_routes_resolve_to_components(tmp_path):
    app_root = tmp_path / "apps" / "web"
    page_dir = app_root / "src" / "app" / "(auth)" / "login"
    page_dir.mkdir(parents=True)

    (app_root / "package.json").write_text(
        '{"dependencies":{"next":"14.2.35","react":"18.3.1"}}',
        encoding="utf-8",
    )
    (page_dir / "page.tsx").write_text(
        "export default function Page() {\n"
        "  return <main>Login</main>;\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    page = next(
        n
        for n in result.nodes
        if n.path == "apps/web/src/app/(auth)/login/page.tsx"
        and n.name == "Page"
    )
    route = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.ROUTE
        and n.qualified_name == "GET /login"
    )

    assert page.kind is CodeNodeKind.COMPONENT
    assert route.metadata.get("framework") == "next"
    assert route.metadata.get("router") == "app"
    assert route.metadata.get("route_type") == "page"
    assert any(
        e.kind is CodeEdgeKind.ROUTES_TO
        and e.source_id == route.node_id
        and e.target_id == page.node_id
        and e.metadata.get("resolved") is True
        for e in result.edges
    )


def test_react_cross_file_component_use_resolves(tmp_path):
    (tmp_path / "Header.tsx").write_text(
        "export function Header() {\n"
        "  return <header>Header</header>;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "page.tsx").write_text(
        'import { Header } from "./Header";\n'
        "export default function Page() {\n"
        "  return <main><Header /></main>;\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    page = next(
        n for n in result.nodes
        if n.path == "page.tsx"
        and n.name == "Page"
        and n.kind is CodeNodeKind.COMPONENT
    )
    header = next(
        n for n in result.nodes
        if n.path == "Header.tsx"
        and n.name == "Header"
        and n.kind is CodeNodeKind.COMPONENT
    )

    assert any(
        e.kind is CodeEdgeKind.USES
        and e.source_id == page.node_id
        and e.target_id == header.node_id
        and e.metadata.get("resolved") is True
        for e in result.edges
    )


def test_next_app_router_page_uses_nearest_layout(tmp_path):
    app_root = tmp_path / "apps" / "web"
    app_dir = app_root / "src" / "app"
    page_dir = app_dir / "dashboard"
    page_dir.mkdir(parents=True)

    (app_root / "package.json").write_text(
        '{"dependencies":{"next":"14.2.35","react":"18.3.1"}}',
        encoding="utf-8",
    )
    (app_dir / "layout.tsx").write_text(
        "export default function RootLayout({ children }) {\n"
        "  return <html><body>{children}</body></html>;\n"
        "}\n",
        encoding="utf-8",
    )
    (page_dir / "page.tsx").write_text(
        "export default function Page() {\n"
        "  return <main>Dashboard</main>;\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    route = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.ROUTE
        and n.qualified_name == "GET /dashboard"
    )
    layout = next(
        n
        for n in result.nodes
        if n.path == "apps/web/src/app/layout.tsx"
        and n.name == "RootLayout"
        and n.kind is CodeNodeKind.COMPONENT
    )

    assert any(
        e.kind is CodeEdgeKind.USES
        and e.source_id == route.node_id
        and e.target_id == layout.node_id
        and e.metadata.get("framework") == "next"
        and e.metadata.get("role") == "layout"
        for e in result.edges
    )


def test_next_app_router_dynamic_page_routes(tmp_path):
    app_root = tmp_path / "apps" / "web"
    app_dir = app_root / "src" / "app"

    (app_root).mkdir(parents=True)
    (app_root / "package.json").write_text(
        '{"dependencies":{"next":"14.2.35","react":"18.3.1"}}',
        encoding="utf-8",
    )

    for relative in (
        "orders/[id]/page.tsx",
        "docs/[...slug]/page.tsx",
        "shop/[[...slug]]/page.tsx",
    ):
        path = app_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "export default function Page() {\n"
            "  return <main>Dynamic</main>;\n"
            "}\n",
            encoding="utf-8",
        )

    result = scan_polyglot_project("demo", tmp_path)

    routes = {
        n.qualified_name
        for n in result.nodes
        if n.kind is CodeNodeKind.ROUTE
        and n.metadata.get("framework") == "next"
        and n.metadata.get("route_type") == "page"
    }

    assert "GET /orders/[id]" in routes
    assert "GET /docs/[...slug]" in routes
    assert "GET /shop/[[...slug]]" in routes


def test_next_app_router_page_incremental_resolves_from_seed_symbols(tmp_path):
    app_root = tmp_path / "apps" / "web"
    page_dir = app_root / "src" / "app" / "dashboard"
    page_dir.mkdir(parents=True)

    (app_root / "package.json").write_text(
        '{"dependencies":{"next":"14.2.35","react":"18.3.1"}}',
        encoding="utf-8",
    )
    (page_dir / "page.tsx").write_text(
        "export default function Page() {\n"
        "  return <main>Dashboard</main>;\n"
        "}\n",
        encoding="utf-8",
    )

    full = scan_polyglot_project("demo", tmp_path)
    page = next(
        n
        for n in full.nodes
        if n.path == "apps/web/src/app/dashboard/page.tsx"
        and n.name == "Page"
        and n.kind is CodeNodeKind.COMPONENT
    )
    seed_symbols = {
        n.qualified_name: n.node_id
        for n in full.nodes
        if n.qualified_name
    }

    partial = scan_polyglot_project(
        "demo",
        tmp_path,
        include_paths={"apps/web/src/app/dashboard/page.tsx"},
        seed_symbols=seed_symbols,
    )

    route = next(
        n
        for n in partial.nodes
        if n.kind is CodeNodeKind.ROUTE
        and n.qualified_name == "GET /dashboard"
    )

    assert any(
        e.kind is CodeEdgeKind.ROUTES_TO
        and e.source_id == route.node_id
        and e.target_id == page.node_id
        and e.metadata.get("resolved") is True
        for e in partial.edges
    )


def test_next_page_fetch_resolves_to_api_route(tmp_path):
    app_root = tmp_path / "apps" / "web"
    app_dir = app_root / "src" / "app"
    api_dir = app_dir / "api" / "orders"
    page_dir = app_dir / "orders"
    api_dir.mkdir(parents=True)
    page_dir.mkdir(parents=True)

    (app_root / "package.json").write_text(
        '{"dependencies":{"next":"14.2.35","react":"18.3.1"}}',
        encoding="utf-8",
    )
    (api_dir / "route.ts").write_text(
        "export async function GET() {\n"
        "  return Response.json([]);\n"
        "}\n",
        encoding="utf-8",
    )
    (page_dir / "page.tsx").write_text(
        "export default async function Page() {\n"
        '  const response = await fetch("/api/orders");\n'
        "  const orders = await response.json();\n"
        "  return <main>{orders.length}</main>;\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    page = next(
        n
        for n in result.nodes
        if n.path == "apps/web/src/app/orders/page.tsx"
        and n.name == "Page"
        and n.kind is CodeNodeKind.COMPONENT
    )
    api_route = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.ROUTE
        and n.qualified_name == "GET /api/orders"
    )

    assert any(
        e.kind is CodeEdgeKind.CALLS
        and e.source_id == page.node_id
        and e.target_id == api_route.node_id
        and e.metadata.get("resolved") is True
        and e.metadata.get("js_call_kind") == "fetch_route"
        for e in result.edges
    )


def test_next_fetch_post_resolves_to_post_api_route(tmp_path):
    app_root = tmp_path / "apps" / "web"
    app_dir = app_root / "src" / "app"
    api_dir = app_dir / "api" / "orders"
    page_dir = app_dir / "orders"
    api_dir.mkdir(parents=True)
    page_dir.mkdir(parents=True)

    (app_root / "package.json").write_text(
        '{"dependencies":{"next":"14.2.35","react":"18.3.1"}}',
        encoding="utf-8",
    )
    (api_dir / "route.ts").write_text(
        "export async function GET() {\n"
        "  return Response.json([]);\n"
        "}\n"
        "export async function POST() {\n"
        "  return Response.json({ ok: true });\n"
        "}\n",
        encoding="utf-8",
    )
    (page_dir / "page.tsx").write_text(
        "export default async function Page() {\n"
        '  await fetch("/api/orders", { method: "POST" });\n'
        "  return <main>Orders</main>;\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    page = next(
        n
        for n in result.nodes
        if n.path == "apps/web/src/app/orders/page.tsx"
        and n.name == "Page"
    )
    post_route = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.ROUTE
        and n.qualified_name == "POST /api/orders"
    )

    assert any(
        e.kind is CodeEdgeKind.CALLS
        and e.source_id == page.node_id
        and e.target_id == post_route.node_id
        and e.metadata.get("resolved") is True
        and e.metadata.get("js_call_kind") == "fetch_route"
        and e.metadata.get("http_method") == "POST"
        for e in result.edges
    )


def test_fetch_template_url_creates_external_http_dependency(tmp_path):
    app_root = tmp_path / "apps" / "web"
    route_dir = app_root / "src" / "app" / "api" / "auth" / "login"
    route_dir.mkdir(parents=True)

    (app_root / "package.json").write_text(
        '{"dependencies":{"next":"14.2.35","react":"18.3.1"}}',
        encoding="utf-8",
    )
    (route_dir / "route.ts").write_text(
        "const gatewayUrl = process.env.GATEWAY_URL;\n"
        "export async function POST() {\n"
        '  return fetch(`${gatewayUrl}/v1/auth/login`, { method: "POST" });\n'
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    handler = next(
        n
        for n in result.nodes
        if n.path == "apps/web/src/app/api/auth/login/route.ts"
        and n.name == "POST"
    )
    target = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.EXTERNAL
        and n.metadata.get("http_dependency") is True
        and n.metadata.get("uri") == "/v1/auth/login"
        and n.metadata.get("http_method") == "POST"
    )

    assert any(
        e.kind is CodeEdgeKind.CALLS
        and e.source_id == handler.node_id
        and e.target_id == target.node_id
        and e.metadata.get("js_call_kind") == "fetch_http"
        for e in result.edges
    )


def test_go_local_module_import_function_resolves(tmp_path):
    util_root = tmp_path / "pkg" / "util"
    service_root = tmp_path / "services" / "demo"
    util_root.mkdir(parents=True)
    service_root.mkdir(parents=True)

    (util_root / "go.mod").write_text(
        "module github.com/example/util\n",
        encoding="utf-8",
    )
    (service_root / "go.mod").write_text(
        "module github.com/example/demo\n",
        encoding="utf-8",
    )

    (util_root / "util.go").write_text(
        "package util\n\n"
        "func Hash() string {\n"
        '    return "ok"\n'
        "}\n",
        encoding="utf-8",
    )
    (service_root / "main.go").write_text(
        "package demo\n\n"
        'import util "github.com/example/util"\n\n'
        "func Run() string {\n"
        "    return util.Hash()\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    run = next(
        n for n in result.nodes
        if n.path == "services/demo/main.go" and n.name == "Run"
    )
    target = next(
        n for n in result.nodes
        if n.path == "pkg/util/util.go" and n.name == "Hash"
    )

    assert any(
        e.kind is CodeEdgeKind.CALLS
        and e.source_id == run.node_id
        and e.target_id == target.node_id
        and e.metadata.get("resolved") is True
        for e in result.edges
    )


def test_go_chained_selector_emits_only_outermost_chain_target(tmp_path):
    (tmp_path / "main.go").write_text(
        "package demo\n\n"
        'import log "github.com/example/log"\n\n'
        "func Run() {\n"
        '    log.New().With().Str("key", "value").Msg("done")\n'
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    chained = [
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.EXTERNAL
        and n.metadata.get("go_call_kind") == "chained_selector"
    ]

    assert len(chained) == 1
    assert chained[0].name.endswith('.Msg')


def test_http_dependency_resolves_to_go_prefix_route_table(tmp_path):
    web_dir = tmp_path / "apps" / "web"
    gateway_dir = (
        tmp_path
        / "services"
        / "gateway-service"
        / "internal"
        / "route"
    )
    web_dir.mkdir(parents=True)
    gateway_dir.mkdir(parents=True)

    (web_dir / "client.ts").write_text(
        "const gatewayUrl = process.env.GATEWAY_URL;\n"
        "export async function login() {\n"
        '  return fetch(`${gatewayUrl}/v1/auth/login`, { method: "POST" });\n'
        "}\n",
        encoding="utf-8",
    )

    (gateway_dir / "routes.go").write_text(
        'package route\n\n'
        'import "net/http"\n\n'
        "type Route struct {\n"
        "    Method string\n"
        "    Prefix string\n"
        "    Backend string\n"
        "    Public bool\n"
        "}\n\n"
        "func Table() []Route {\n"
        "    return []Route{\n"
        '        {Prefix: "/v1/auth/", Backend: "identity", Public: true},\n'
        '        {Method: http.MethodGet, Prefix: "/health", Backend: "identity"},\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    login = next(
        n
        for n in result.nodes
        if n.path == "apps/web/client.ts"
        and n.name == "login"
    )
    gateway_route = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.ROUTE
        and n.metadata.get("framework") == "go_http_route_table"
        and n.metadata.get("uri_prefix") == "/v1/auth/"
        and n.metadata.get("backend") == "identity"
    )

    assert any(
        e.kind is CodeEdgeKind.CALLS
        and e.source_id == login.node_id
        and e.target_id == gateway_route.node_id
        and e.metadata.get("resolved") is True
        and e.metadata.get("resolution") == "http_prefix_route"
        for e in result.edges
    )


def test_manifest_scanner_maps_docker_compose_package_and_workflow(tmp_path):
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.13-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        'CMD ["python", "-m", "app"]\n',
        encoding="utf-8",
    )

    (tmp_path / "compose.yml").write_text(
        "services:\n"
        "  api:\n"
        "    build: .\n"
        "    ports:\n"
        '      - "8080:8080"\n'
        "  db:\n"
        "    image: postgres:17\n",
        encoding="utf-8",
    )

    (tmp_path / "package.json").write_text(
        '{"name":"demo-web","dependencies":{"react":"18.3.1"}}',
        encoding="utf-8",
    )

    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        "name: CI\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    assert any(
        n.kind is CodeNodeKind.IMAGE
        and n.metadata.get("manifest") == "dockerfile"
        for n in result.nodes
    )
    assert any(
        n.kind is CodeNodeKind.SERVICE
        and n.name == "api"
        and n.metadata.get("manifest") == "compose"
        for n in result.nodes
    )
    package = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.PACKAGE
        and n.name == "demo-web"
    )
    react = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.EXTERNAL
        and n.metadata.get("package_manager") == "npm"
        and n.name == "react"
    )
    assert any(
        e.kind is CodeEdgeKind.DEPENDS_ON
        and e.source_id == package.node_id
        and e.target_id == react.node_id
        for e in result.edges
    )
    assert any(
        n.kind is CodeNodeKind.CONFIG
        and n.metadata.get("manifest") == "github_actions"
        for n in result.nodes
    )


def test_terraform_resources_modules_and_dependencies(tmp_path):
    (tmp_path / "main.tf").write_text(
        '''
resource "aws_s3_bucket" "assets" {
  bucket = "demo-assets"
}

resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id
}

module "network" {
  source = "./modules/network"
}
''',
        encoding="utf-8",
    )

    result = scan_polyglot_project("demo", tmp_path)

    bucket = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.RESOURCE
        and n.name == "aws_s3_bucket.assets"
    )
    versioning = next(
        n
        for n in result.nodes
        if n.kind is CodeNodeKind.RESOURCE
        and n.name == "aws_s3_bucket_versioning.assets"
    )

    assert any(
        n.kind is CodeNodeKind.MODULE
        and n.name == "network"
        and n.metadata.get("terraform_source") == "./modules/network"
        for n in result.nodes
    )

    assert any(
        e.kind is CodeEdgeKind.DEPENDS_ON
        and e.source_id == versioning.node_id
        and e.target_id == bucket.node_id
        for e in result.edges
    )
