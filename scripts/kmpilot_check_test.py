#!/usr/bin/env python3
"""Self-test for `.claude/skills/_shared/kmpilot_check.py`.

Builds a throwaway fixture repo in a temp dir and asserts two things:

  * `clean` — a feature obeying every rule, including every Rule 12 allowlist
    branch, produces ZERO violations. This is the false-positive guard: a checker
    that cries wolf gets disabled on day one.
  * `probe` — a feature carrying one injected violation per mechanized check.
    Every check ID must fire, at the expected file, severity and line.

Also covers: test source sets are invisible, the sanctioned `DataModules.kt`
exception, the `app`-tier self-import exemption, and `--baseline`.

    python3 scripts/kmpilot_check_test.py

Exits 0 on success. Runs in ~1s, no network, no Gradle.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / ".claude/skills/_shared/kmpilot_check.py"
PKG = "probeco"


class Fixture:
    def __init__(self, base: Path):
        self.root = base / "fixture-repo"

    def w(self, rel: str, body: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.lstrip("\n"), encoding="utf-8")

    def build(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self._scaffold()
        self._clean_feature()
        self._probe_feature()

    # ── repo scaffolding ────────────────────────────────────────────────────
    def _scaffold(self) -> None:
        self.w("settings.gradle.kts", """
include(":composeApp")
include(":feature:clean")
""")
        self.w("composeApp/build.gradle.kts", """
dependencies {
    implementation(project(":feature:clean"))
}
""")
        # `cleanModule` is registered through an aggregating `val` in a SIBLING file,
        # not named at the `modules(...)` call site. That is the shape
        # `install.sh --adopt` writes when a project has no `startKoin` of its own
        # (`modules(kmpilotModules)` + a `kmpilotModules` list next door), and I3 used to
        # read only the call site — so a feature registered exactly as adopt's own
        # scaffold prescribes was reported missing, with no edit to the feature able to
        # clear it. `clean` passing I3 here is what proves the indirection hop works;
        # `probe` still failing it is what proves the hop is not over-broad.
        # The import of `probeModule` is deliberate and is a trap: `probe` is imported
        # but never added to any list, which is what a half-finished registration looks
        # like. I3 must still report it missing. Without this line the "follow one hop"
        # relaxation could be widened to "the name appears anywhere in the app module"
        # and nothing would notice.
        self.w(f"composeApp/src/commonMain/kotlin/{PKG}/app/initKoin.kt", f"""
package {PKG}.app

import {PKG}.probe.di.probeModule

fun initKoin() =
    startKoin {{
        modules(
            appModule,
            featureModules,
        )
    }}
""")
        self.w(f"composeApp/src/commonMain/kotlin/{PKG}/app/FeatureModules.kt", f"""
package {PKG}.app

val featureModules: List<Module> =
    listOf(
        cleanModule,
    )
""")
        self.w(f"composeApp/src/commonMain/kotlin/{PKG}/app/BaseAppNavHost.kt", f"""
package {PKG}.app

@Composable
fun BaseAppNavHost(modifier: Modifier) {{
    XNavHost(modifier = modifier) {{
        clean(onBackClick = {{}})
    }}
}}
""")
        # :core:common is where {PKG_PREFIX} comes from. Deliberately placed in a
        # SUB-package (`common.util`, not `common`) — resolution must cut at the
        # `common` segment, not strip the last one.
        self.w(f"core/common/src/commonMain/kotlin/{PKG}/common/util/UiState.kt", f"""
package {PKG}.common.util

sealed interface UiState<out T>
""")
        # S3: a generic core file importing the stripped `.app` tier.
        self.w(f"core/designsystem/src/commonMain/kotlin/{PKG}/designsystem/XButton.kt", f"""
package {PKG}.designsystem

import androidx.compose.runtime.Composable
import {PKG}.designsystem.app.AppErrorState

@Composable
fun XButton() = AppErrorState()
""")
        # The one sanctioned exception — must NOT be flagged.
        self.w(f"core/data/src/commonMain/kotlin/{PKG}/data/di/DataModules.kt", f"""
package {PKG}.data.di

import {PKG}.data.app.appDataModule

val dataModule = module {{ includes(appDataModule) }}
""")
        # A file already inside the `app` tier may import it — must NOT be flagged.
        self.w(f"core/designsystem/src/commonMain/kotlin/{PKG}/designsystem/app/AppErrorState.kt", f"""
package {PKG}.designsystem.app

import {PKG}.designsystem.app.internal.Helper

fun AppErrorState() = Helper()
""")

    # ── the clean feature: must be completely silent ────────────────────────
    def _clean_feature(self) -> None:
        self.w("feature/clean/build.gradle.kts", "// clean feature\n")
        self.w("feature/clean/src/commonMain/composeResources/values/strings.xml", """
<resources>
    <string name="clean_title">Clean</string>
</resources>
""")
        self.w(f"feature/clean/src/commonMain/kotlin/{PKG}/clean/di/CleanModules.kt", f"""
package {PKG}.clean.di

val cleanModule =
    module {{
        singleOf(::CleanRepositoryImpl).bind<CleanRepository>()
        viewModelOf(::CleanViewModel)
    }}
""")
        self.w(f"feature/clean/src/commonMain/kotlin/{PKG}/clean/data/model/CleanData.kt", f"""
package {PKG}.clean.data.model

data class CleanData(val name: String, val price: Double)
""")
        self.w(f"feature/clean/src/commonMain/kotlin/{PKG}/clean/data/repository/CleanRepository.kt", f"""
package {PKG}.clean.data.repository

interface CleanRepository {{
    suspend fun load(): Either<CleanData>
}}
""")
        self.w(f"feature/clean/src/commonMain/kotlin/{PKG}/clean/presentation/CleanUiModel.kt", f"""
package {PKG}.clean.presentation

data class CleanUiModel(
    val dataState: UiState<CleanData> = UiState.Uninitialized,
)
""")
        self.w(f"feature/clean/src/commonMain/kotlin/{PKG}/clean/presentation/CleanViewModel.kt", f"""
package {PKG}.clean.presentation

class CleanViewModel {{
    private val _uiModel = MutableStateFlow(CleanUiModel())

    fun retry() {{
        _uiModel.setState {{ copy(dataState = UiState.Loading) }}
        // reading .value is fine; only assignment is a violation
        val current = _uiModel.value
    }}
}}
""")
        self.w(f"feature/clean/src/commonMain/kotlin/{PKG}/clean/presentation/ui/CleanScreen.kt", f"""
package {PKG}.clean.presentation.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.tooling.preview.Preview

@Composable
fun CleanScreen(viewModel: CleanViewModel, onBackClick: () -> Unit) {{
    val uiModel by viewModel.uiModel.collectAsStateWithLifecycle()
    CleanScreenRoot(uiModel = uiModel, onBackClick = onBackClick)
}}

@Composable
fun CleanScreenRoot(uiModel: CleanUiModel, onBackClick: () -> Unit) {{
    XScreen(topBar = {{ XTopAppBar(title = stringResource(Res.string.clean_title)) }}) {{
        when (val state = uiModel.dataState) {{
            UiState.Uninitialized -> EmptyContent()
            UiState.Loading -> AppLoadingState()
            is UiState.Failed -> AppErrorState(
                title = stringResource(Res.string.clean_title),
                message = stringResource(Res.string.clean_title),
                onRetry = {{}},
            )
            is UiState.Success -> CleanContent(data = state.value)
        }}
    }}
}}

@Composable
private fun EmptyContent() {{
    XText(text = stringResource(Res.string.clean_title))
}}

private val sampleCleanData = CleanData(name = "Bitcoin", price = 1.0)

@Preview
@Composable
private fun CleanScreenRootPreview() {{
    XTheme {{
        XText(text = "Preview only copy")
        CleanScreenRoot(uiModel = CleanUiModel(), onBackClick = {{}})
    }}
}}
""")
        self.w(f"feature/clean/src/commonMain/kotlin/{PKG}/clean/presentation/ui/CleanUtils.kt", f"""
package {PKG}.clean.presentation.ui

fun formatPrice(value: Double): String = value.toString()
""")
        # Every Rule 12 allowlist branch lives here: single glyph, interpolated
        # repository data, glyph + data, animation debug label, control sentinel,
        # plus the sanctioned plain navigationBarsPadding() on a scroll list.
        self.w(f"feature/clean/src/commonMain/kotlin/{PKG}/clean/presentation/ui/components/CleanContent.kt", f"""
package {PKG}.clean.presentation.ui.components

@Composable
fun CleanContent(data: CleanData) {{
    LazyColumn(
        modifier = Modifier.fillMaxSize().navigationBarsPadding(),
    ) {{
        item {{
            XText(text = "$")
            XText(text = "${{data.name}} (${{data.price}})")
            XText(text = "$${{data.price}}")
            XText(text = stringResource(Res.string.clean_title))
        }}
        item {{
            val amount by animateFloatAsState(
                targetValue = 1f,
                label = "animatedAmount",
            )
            XTextButton(onClick = {{}}) {{
                XText(text = quickLabel)
            }}
        }}
    }}
}}

private val quickLabel: String
    get() = if (isMax) "MAX" else ""

private val isMax: Boolean
    get() = selected == "MAX"
""")

    # ── the probe feature: one violation per check ──────────────────────────
    def _probe_feature(self) -> None:
        self.w("feature/probe/build.gradle.kts", "// probe feature\n")
        # S6 — a screen sitting flat in the package root instead of presentation/ui/.
        # This is the shape a migrated feature arrives in, and it is why S6 exists:
        # R3, R12, R13 and S1 are all path-gated on presentation/ui, so without this
        # rule a feature in this layout reports zero findings because those checks
        # never ran — and a migration would sign off work it never did.
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/StrayScreen.kt", f"""
package {PKG}.probe

@Composable
fun StrayScreen() {{
}}
""")
        # R3
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/presentation/ProbeViewModel.kt", f"""
package {PKG}.probe.presentation

class ProbeViewModel {{
    private val _uiModel = MutableStateFlow(ProbeUiModel())

    fun load() {{
        _uiModel.value = _uiModel.value.copy(loading = true)
    }}
}}
""")
        # R11a
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/presentation/ProbeUiState.kt", f"""
package {PKG}.probe.presentation

data class ProbeUiState(val state: UiState<ProbeUiModel> = UiState.Uninitialized)
""")
        # R11b — two UiModels
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/presentation/ProbeUiModel.kt", f"""
package {PKG}.probe.presentation

data class ProbeUiModel(val loading: Boolean = false)
""")
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/presentation/ProbeDetailUiModel.kt", f"""
package {PKG}.probe.presentation

data class ProbeDetailUiModel(val loading: Boolean = false)
""")
        # R11c
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/data/repository/ProbeRepositoryImpl.kt", f"""
package {PKG}.probe.data.repository

import {PKG}.probe.presentation.ProbeUiModel

class ProbeRepositoryImpl : ProbeRepository
""")
        # R7
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/data/model/ProbeData.kt", f"""
package {PKG}.probe.data_model.Probe

data class ProbeData(val name: String)
""")
        # R9
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/data/usecase/LoadProbeUseCase.kt", f"""
package {PKG}.probe.data.usecase

class LoadProbeUseCase(private val repository: ProbeRepository)
""")
        # R8 — module val misnamed and no .bind<>
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/di/ProbeModules.kt", f"""
package {PKG}.probe.di

val probeFeatureModule =
    module {{
        singleOf(::ProbeRepositoryImpl)
    }}
""")
        # R5, R12, R13, S1, S4
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/presentation/ui/ProbeScreen.kt", f"""
package {PKG}.probe.presentation.ui

import androidx.compose.material3.Button
import org.jetbrains.compose.ui.tooling.preview.Preview

@Composable
fun ProbeScreen(viewModel: ProbeViewModel) {{
    ProbeScreenRoot(uiModel = viewModel.uiModel.value)
}}

@Composable
fun ProbeScreenRoot(uiModel: ProbeUiModel) {{
    Scaffold(
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        bottomBar = {{ ProbeBottomBar() }},
    ) {{
        XText(text = "Hardcoded probe title")
    }}
}}

@Composable
private fun ProbeBottomBar() {{
    XButton(modifier = Modifier.navigationBarsPadding(), onClick = {{}}) {{
        XText(text = stringResource(Res.string.probe_cta))
    }}
}}
""")
        # S2
        self.w(f"feature/probe/src/commonMain/kotlin/{PKG}/probe/presentation/ui/components/ProbeFormatters.kt", f"""
package {PKG}.probe.presentation.ui.components

fun formatProbe(value: Double): String = value.toString()
""")
        # Test source sets must be invisible.
        self.w(f"feature/probe/src/commonTest/kotlin/{PKG}/probe/ProbeViewModelTest.kt", f"""
package {PKG}.probe

class ProbeViewModelTest {{
    fun `test`() {{
        _uiModel.value = ProbeUiModel()
        XText(text = "Test literal")
    }}
}}
""")


# Each mechanized check, the file it must be reported against, and its severity.
EXPECTED = {
    "R3": ("ProbeViewModel.kt", "error"),
    "R5": ("ProbeScreen.kt", "error"),
    "R7": ("ProbeData.kt", "error"),
    "R8": ("ProbeModules.kt", "error"),
    "R9": ("LoadProbeUseCase.kt", "error"),
    "R11a": ("ProbeUiState.kt", "error"),
    "R11b": ("UiModel.kt", "error"),
    "R11c": ("ProbeRepositoryImpl.kt", "error"),
    "R12": ("ProbeScreen.kt", "error"),
    "R13": ("ProbeScreen.kt", "error"),
    "S1": ("ProbeScreen.kt", "warning"),
    "S2": ("ProbeFormatters.kt", "warning"),
    "S3": ("XButton.kt", "error"),
    "S4": ("ProbeScreen.kt", "warning"),
    "S6": ("StrayScreen.kt", "error"),
    # Repo-scoped, so it fires on the fixture's app module, not on `probe`. The fixture's
    # shell deliberately has neither mechanism — the shape has to be IN the fixture or the
    # rule is not testable at all, which is the lesson S6 cost (a mutation that never
    # fires survives against a fixture with no instance of the shape).
    "S7": ("composeApp", "warning"),
    "I1": ("settings.gradle.kts", "error"),
    "I2": ("composeApp/build.gradle.kts", "error"),
    "I3": ("initKoin.kt", "error"),
    "I4": ("BaseAppNavHost.kt", "error"),
}
NO_LINE_EXPECTED = {"I1", "I2", "I3", "I4", "R12", "S7"}

# S7 fires only when the app module has NEITHER mechanism. Three real shells conform in
# three different ways and one of them (a bare `Scaffold` on its default `systemBars`)
# would fail any check that graded *which* mechanism — so each of these must be silent,
# and wrongly failing a project that works is the failure this phase has paid for twice.
SHELL_SHAPES = {
    "scaffold-and-insets": (
        "XScaffold(contentWindowInsets = WindowInsets(0, 0, 0, 0)) { _ ->\n"
        "        Content(Modifier.windowInsetsPadding(WindowInsets.safeDrawing))\n"
        "    }"
    ),
    "scaffold-only": "Scaffold { padding -> Content(Modifier.padding(padding)) }",
    "insets-only": "Column(Modifier.systemBarsPadding()) { Content() }",
}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Fixture(Path(tmp))
        fixture.build()
        report_path = Path(tmp) / "check-report.json"

        def run(*args: str) -> tuple[int, dict]:
            proc = subprocess.run(
                [
                    sys.executable, str(CHECKER),
                    "--root", str(fixture.root),
                    "--report", str(report_path),
                    *args,
                ],
                capture_output=True,
                text=True,
            )
            if not report_path.is_file():
                raise SystemExit(f"checker wrote no report:\n{proc.stdout}\n{proc.stderr}")
            return proc.returncode, json.loads(report_path.read_text())

        failures: list[str] = []

        # ── the prefix must resolve past the `common.util` sub-package ─────
        code, report = run("clean")
        if report["pkgPrefix"] != PKG:
            failures.append(f"pkgPrefix {report['pkgPrefix']!r}, expected {PKG!r}")

        # ── false-positive guard ───────────────────────────────────────────
        # S3 is repo-scoped (feature "-") and the fixture plants one on purpose,
        # so the guard looks only at feature-scoped findings.
        clean_v = [v for v in report["violations"] if v["feature"] == "clean"]
        if clean_v:
            failures.append(
                "clean feature produced violations (false positives):\n"
                + "\n".join(
                    f"    {v['rule']} {v['file']}:{v['line']} {v['message']}" for v in clean_v
                )
            )
        if code != 1:
            failures.append(f"clean run exit code {code}, expected 1 from the planted S3")

        # ── negative test: every check must fire ───────────────────────────
        code, report = run("probe")
        hits: dict[str, list[dict]] = {}
        for v in report["violations"]:
            hits.setdefault(v["rule"], []).append(v)

        for rule, (name_part, severity) in EXPECTED.items():
            got = hits.get(rule)
            if not got:
                failures.append(f"{rule} did not fire")
                continue
            if not any(name_part in v["file"] for v in got):
                failures.append(
                    f"{rule} fired on {[v['file'] for v in got]}, expected a file matching {name_part!r}"
                )
            if not any(v["severity"] == severity for v in got):
                failures.append(
                    f"{rule} severity {[v['severity'] for v in got]}, expected {severity!r}"
                )
            if rule not in NO_LINE_EXPECTED and not any(v["line"] > 0 for v in got):
                failures.append(f"{rule} reported no line number")

        if not any("strings.xml" in v["file"] for v in hits.get("R12", [])):
            failures.append("R12 strings.xml sub-check did not fire")
        if code != 1:
            failures.append(f"probe exit code {code}, expected 1")
        if any("commonTest" in v["file"] for v in report["violations"]):
            failures.append("violations reported inside a test source set")

        # ── S7: the three conforming shells must each be silent ────────────
        shell_path = fixture.root / f"composeApp/src/commonMain/kotlin/{PKG}/app/App.kt"
        for label, body in SHELL_SHAPES.items():
            shell_path.write_text(
                f"package {PKG}.app\n\n@Composable\nfun App() {{\n    {body}\n}}\n",
                encoding="utf-8",
            )
            _, shaped = run("probe")
            fired = [v for v in shaped["violations"] if v["rule"] == "S7"]
            if fired:
                failures.append(
                    f"S7 fired on the {label} shell, which provides the safe area: "
                    f"{fired[0]['message']}"
                )
        # A `Scaffold` named only in a comment or a string is not a shell: the mechanisms
        # are matched against code with comments and literals blanked, so this must still
        # fire. Without it, "the word appears in the app module" would pass every test.
        shell_path.write_text(
            f"package {PKG}.app\n\n"
            "// TODO: wrap this in a Scaffold and add windowInsetsPadding(safeDrawing)\n"
            f'const val NOTE = "Scaffold + windowInsetsPadding go here"\n\n'
            "@Composable\nfun App() {\n    Content()\n}\n",
            encoding="utf-8",
        )
        _, commented = run("probe")
        if not [v for v in commented["violations"] if v["rule"] == "S7"]:
            failures.append(
                "S7 stayed silent on a shell that only mentions `Scaffold` in a comment and "
                "a string literal — the mechanisms must be matched in code, not in prose"
            )
        shell_path.unlink()

        # ── --baseline downgrades and exits 0 ──────────────────────────────
        code, report = run("probe", "--baseline")
        if code != 0:
            failures.append(f"--baseline exit code {code}, expected 0")
        if report["mode"] != "baseline":
            failures.append(f"--baseline report mode {report['mode']!r}")
        if any(v["severity"] == "error" for v in report["violations"]):
            failures.append("--baseline left error-severity violations")
        if not any(v.get("strictSeverity") == "error" for v in report["violations"]):
            failures.append("--baseline did not record strictSeverity")

        print(f"probe fired {len(hits)} distinct checks: {' '.join(sorted(hits))}")
        print(f"checks reported: {report['summary']['checked']}")
        if failures:
            print("\nFAILURES:")
            for f in failures:
                print(f"  x {f}")
            return 1
        print("\nPASS — every check fires, clean feature silent")
        return 0


if __name__ == "__main__":
    sys.exit(main())
