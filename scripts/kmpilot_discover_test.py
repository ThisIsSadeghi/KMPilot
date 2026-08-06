#!/usr/bin/env python3
"""Self-test for `.claude/skills/_shared/kmpilot_discover.py`.

Builds one throwaway fixture project in a temp dir — an *adopted*, deliberately
non-conforming KMP repo — and asserts three things:

  * **every classifier fires** at the expected subject: module kinds, feature
    verdicts, refusals (Android-locked / no entry point / unhoistable shared code),
    tier proposals for all four outcomes (common / data / designsystem / split),
    the cross-feature edge, the dependency cycle, and each note.
  * **the false-positive guard holds** — the conforming feature produces no refusal
    and no Android evidence, including for the two APIs that look Android-only and
    are not: `androidx.navigation.*` and `androidx.lifecycle.ViewModel`. An
    `android.content.Context` import inside `androidMain` is likewise expected, not
    blocking.
  * **discovery writes nothing.** The fixture's file list is captured before and
    after the run and must be byte-for-byte identical. This is the one guarantee the
    whole phase leans on: the plan is confirmed by a human before anything is
    written, and a discovery pass with side effects breaks it silently.

    python3 scripts/kmpilot_discover_test.py

Exits 0 on success. Runs in ~1s, no network, no Gradle.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DISCOVER = REPO / ".claude/skills/_shared/kmpilot_discover.py"
PKG = "probeco"


class Fixture:
    """An adopted KMP project holding one of everything discovery has to recognise."""

    def __init__(self, base: Path):
        self.root = base / "discover-fixture"

    def w(self, rel: str, body: str) -> None:
        """`@PKG@` in `body` is replaced with the package prefix — a placeholder
        rather than an f-string so Kotlin/Gradle braces need no escaping."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.lstrip("\n").replace("@PKG@", PKG), encoding="utf-8")

    def kt(self, module: str, pkg_path: str, name: str, body: str, sourceset: str = "commonMain") -> None:
        self.w(f"{module}/src/{sourceset}/kotlin/{PKG}/{pkg_path}/{name}", body)

    def build(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self._scaffold()
        self._vendored_core()
        self._host_shared()
        self._conforming_feature()
        self._messy_feature()
        self._legacy_feature()
        self._headless_feature()
        self._root_level_feature()
        self._cycle()

    # ── project scaffolding ─────────────────────────────────────────────────
    def _scaffold(self) -> None:
        self.w("settings.gradle.kts", """
rootProject.name = "ProbeCo"

dependencyResolutionManagement {
    versionCatalogs {
        create("kmpilotLibs") { from(files("gradle/kmpilot.versions.toml")) }
    }
}

include(":shared")
include(":core:common")
include(":core:data")
include(":core:designsystem")
include(":core:model")
include(":core:netcall")
include(":core:widgets")
include(":core:mixed")
include(":core:androidutil")
include(":feature:conforming")
include(":feature:messy")
include(":feature:legacy")
include(":feature:headless")
include(":oldscreen")
include(":feature:cyclea")
include(":feature:cycleb")
// A commented-out include must NOT become a module.
// include(":feature:ghost")
""")
        self.w(".kmpilot.json", """
{
  "kmpilotVersion": "0.2.1",
  "projectName": "ProbeCo",
  "packagePrefix": "@PKG@",
  "installMode": "adopt",
  "appModule": "shared",
  "catalogAccessor": "kmpilotLibs",
  "koinBootstrap": "host",
  "managedFeatures": [],
  "installedAt": "2026-08-05T00:00:00Z"
}
""")
        # Two catalogs in play — the `catalog-split` note depends on both existing
        # AND on modules actually referencing each accessor.
        self.w("gradle/libs.versions.toml", """
[libraries]
retrofit-core = { module = "com.squareup.retrofit2:retrofit", version = "2.11.0" }
ktor-core = { module = "io.ktor:ktor-client-core", version = "3.0.0" }
""")
        self.w("gradle/kmpilot.versions.toml", """
[libraries]
koin-core = { module = "io.insert-koin:koin-core", version = "4.0.0" }
""")
        # The app module: KMP, all three targets, holds initKoin + the NavHost, and
        # imports every feature's screen. Those imports are Integration Point 4 and
        # must NOT be reported as shared-code-inside-a-feature.
        self.w("shared/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    iosSimulatorArm64()
    jvm("desktop")

    sourceSets {
        commonMain.dependencies {
            implementation(project(":core:common"))
            implementation(project(":feature:conforming"))
            implementation(project(":feature:messy"))
            implementation(kmpilotLibs.koin.core)
        }
    }
}
""")
        self.w(f"shared/src/commonMain/kotlin/{PKG}/app/initKoin.kt", """
package @PKG@.app

fun initKoin() =
    startKoin {
        modules(
            conformingModule,
            messyModule,
        )
    }
""")
        self.w(f"shared/src/commonMain/kotlin/{PKG}/app/BaseAppNavHost.kt", """
package @PKG@.app

import @PKG@.conforming.presentation.navigation.conforming

@Composable
fun BaseAppNavHost(modifier: Modifier) {
    XNavHost(modifier = modifier) {
        conforming(onBackClick = {})
        messy(onBackClick = {})
    }
}
""")

    # ── the vendored :core:* adopt writes — must classify as core-kmpilot ────
    def _vendored_core(self) -> None:
        for module in ("common", "data", "designsystem"):
            self.w(f"core/{module}/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64 { }
    jvm("desktop")
    sourceSets { commonMain.dependencies { implementation(kmpilotLibs.koin.core) } }
}
""")
        # Deliberately in a sub-package: the package prefix must resolve by cutting at
        # the `common` segment, not by stripping the last one.
        self.kt("core/common", "common/util", "UiState.kt", """
package @PKG@.common.util

sealed interface UiState<out T>
""")
        self.kt("core/data", "data/di", "DataModules.kt", """
package @PKG@.data.di

val dataModule = module { }
""")
        self.kt("core/designsystem", "designsystem", "XText.kt", """
package @PKG@.designsystem

import androidx.compose.runtime.Composable

@Composable
fun XText(text: String) = Unit
""")

    # ── the host's own shared modules: one per tier outcome ─────────────────
    def _host_shared(self) -> None:
        host_gradle = """
kotlin {
    androidTarget()
    iosArm64()
    sourceSets { commonMain.dependencies { implementation(libs.ktor.core) } }
}
"""
        # → common.app: plain value types, no wire/storage/UI markers anywhere.
        self.w("core/model/build.gradle.kts", host_gradle)
        self.kt("core/model", "model", "Money.kt", """
package @PKG@.model

data class Money(val amount: Long, val currency: String)
""")
        # → data.app: Ktor. Consumed by two features, so the DRY corollary applies.
        self.w("core/netcall/build.gradle.kts", host_gradle)
        self.kt("core/netcall", "netcall", "ProbeClient.kt", """
package @PKG@.netcall

import io.ktor.client.HttpClient

class ProbeClient(private val client: HttpClient)
""")
        # → designsystem.app: composables.
        self.w("core/widgets/build.gradle.kts", host_gradle)
        self.kt("core/widgets", "widgets", "Badge.kt", """
package @PKG@.widgets

import androidx.compose.runtime.Composable

@Composable
fun Badge(label: String) = Unit
""")
        # → split: one file per tier, so no single tier can claim the module.
        self.w("core/mixed/build.gradle.kts", host_gradle)
        self.kt("core/mixed", "mixed", "MixedCard.kt", """
package @PKG@.mixed

import androidx.compose.runtime.Composable

@Composable
fun MixedCard() = Unit
""")
        self.kt("core/mixed", "mixed", "MixedDto.kt", """
package @PKG@.mixed

import kotlinx.serialization.Serializable

@Serializable
data class MixedDto(val id: String)
""")
        self.kt("core/mixed", "mixed", "MixedMath.kt", """
package @PKG@.mixed

fun clamp(value: Int): Int = value.coerceIn(0, 10)
""")
        # → UNHOISTABLE: an Android framework type in commonMain. Nothing that
        # consumes it can migrate until this is resolved.
        self.w("core/androidutil/build.gradle.kts", host_gradle)
        self.kt("core/androidutil", "androidutil", "Toaster.kt", """
package @PKG@.androidutil

import android.content.Context

class Toaster(private val context: Context)
""")

    # ── conforming feature: the false-positive guard ─────────────────────────
    def _conforming_feature(self) -> None:
        self.w("feature/conforming/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    jvm("desktop")
    sourceSets { commonMain.dependencies { implementation(kmpilotLibs.koin.core) } }
}
""")
        self.w("feature/conforming/src/commonMain/composeResources/values/strings.xml", """
<resources>
    <string name="conforming_title">Conforming</string>
</resources>
""")
        self.kt("feature/conforming", "conforming/di", "ConformingModules.kt", """
package @PKG@.conforming.di

val conformingModule =
    module {
        singleOf(::ConformingRepositoryImpl).bind<ConformingRepository>()
        viewModelOf(::ConformingViewModel)
    }
""")
        self.kt("feature/conforming", "conforming/data/model", "ConformingData.kt", """
package @PKG@.conforming.data.model

data class ConformingData(val name: String)
""")
        self.kt("feature/conforming", "conforming/data/repository", "ConformingRepository.kt", """
package @PKG@.conforming.data.repository

interface ConformingRepository {
    suspend fun load(): Either<ConformingData>
}
""")
        self.kt("feature/conforming", "conforming/data/repository", "ConformingRepositoryImpl.kt", """
package @PKG@.conforming.data.repository

class ConformingRepositoryImpl : ConformingRepository {
    override suspend fun load(): Either<ConformingData> = Either.Success(ConformingData("x"))
}
""")
        self.kt("feature/conforming", "conforming/presentation", "ConformingUiModel.kt", """
package @PKG@.conforming.presentation

data class ConformingUiModel(
    val dataState: UiState<ConformingData> = UiState.Uninitialized,
)
""")
        # TRAP 1: `androidx.lifecycle.ViewModel` / `viewModelScope` in commonMain is
        # the KMP lifecycle artifact — the base class the pipeline itself targets.
        self.kt("feature/conforming", "conforming/presentation", "ConformingViewModel.kt", """
package @PKG@.conforming.presentation

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope

class ConformingViewModel : ViewModel() {
    private val _uiModel = MutableStateFlow(ConformingUiModel())

    fun retry() {
        _uiModel.setState { copy(dataState = UiState.Loading) }
    }
}
""")
        self.kt("feature/conforming", "conforming/presentation/ui", "ConformingScreen.kt", """
package @PKG@.conforming.presentation.ui

import androidx.compose.runtime.Composable
import org.jetbrains.compose.resources.stringResource

@Composable
fun ConformingScreen(viewModel: ConformingViewModel, onBackClick: () -> Unit) {
    val uiModel by viewModel.uiModel.collectAsStateWithLifecycle()
    ConformingScreenRoot(uiModel = uiModel, onBackClick = onBackClick)
}

@Composable
fun ConformingScreenRoot(uiModel: ConformingUiModel, onBackClick: () -> Unit) {
    XScreen(topBar = { XTopAppBar(title = stringResource(Res.string.conforming_title)) }) {
        ConformingContent(uiModel = uiModel)
    }
}
""")
        self.kt("feature/conforming", "conforming/presentation/ui/components", "ConformingContent.kt", """
package @PKG@.conforming.presentation.ui.components

import androidx.compose.runtime.Composable

@Composable
fun ConformingContent(uiModel: ConformingUiModel) = Unit
""")
        # TRAP 2: `androidx.navigation.*` in commonMain is Compose Multiplatform
        # navigation, not the Android Navigation Component.
        self.kt("feature/conforming", "conforming/presentation/navigation", "ConformingRoute.kt", """
package @PKG@.conforming.presentation.navigation

import androidx.navigation.NavGraphBuilder
import androidx.navigation.toRoute

fun NavGraphBuilder.conforming(onBackClick: () -> Unit) = Unit
""")
        # TRAP 3: an Android framework import inside androidMain is Rule 14 working
        # as designed — expected, never blocking.
        self.kt("feature/conforming", "conforming/data/datasource", "PlatformInfo.android.kt", """
package @PKG@.conforming.data.datasource

import android.content.Context
import android.os.Build

actual class PlatformInfo(private val context: Context)
""", sourceset="androidMain")

    # ── messy feature: portable, findings, no desktop target ────────────────
    def _messy_feature(self) -> None:
        # No `jvm("desktop")` → the missing-desktop-target note. Reads the HOST
        # catalog while :core:* reads kmpilotLibs → the catalog-split note.
        self.w("feature/messy/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    sourceSets {
        commonMain.dependencies {
            implementation(project(":core:model"))
            implementation(project(":core:netcall"))
            implementation(project(":core:androidutil"))
            implementation(project(":feature:conforming"))
            implementation(libs.ktor.core)
        }
    }
}
""")
        # Material3 (R5) + `_state.value =` (R3) + an English literal (R12), and it
        # imports another feature's repository — the cross-feature edge.
        self.kt("feature/messy", "messy", "MessyScreen.kt", """
package @PKG@.messy

import androidx.compose.material3.Text
import androidx.compose.material3.Button
import androidx.compose.runtime.Composable
import @PKG@.conforming.data.repository.ConformingRepository

@Composable
fun MessyScreen() {
    Text(text = "Hardcoded label")
}

class MessyViewModel(private val repo: ConformingRepository) {
    private val _state = MutableStateFlow(MessyState())

    fun load() {
        _state.value = _state.value.copy(loading = true)
    }
}
""")

    # ── legacy feature: Android-locked in commonMain → refusal ──────────────
    def _legacy_feature(self) -> None:
        self.w("feature/legacy/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    sourceSets { commonMain.dependencies { implementation(libs.retrofit.core) } }
}
""")
        self.kt("feature/legacy", "legacy", "LegacyScreen.kt", """
package @PKG@.legacy

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import dagger.hilt.android.lifecycle.HiltViewModel
import retrofit2.http.GET
import javax.inject.Inject

@Composable
fun LegacyScreen() = Unit

class LegacyViewModel @Inject constructor(private val context: Context) {
    val data: LiveData<String> = MutableLiveData()
}
""")
        # A commented-out Android import must NOT fabricate evidence.
        self.kt("feature/legacy", "legacy", "LegacyNotes.kt", """
package @PKG@.legacy

// import androidx.fragment.app.Fragment
/* import androidx.appcompat.app.AppCompatActivity */

class LegacyNotes
""")

    # ── headless feature: no composable at all → refusal ────────────────────
    def _headless_feature(self) -> None:
        self.w("feature/headless/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    jvm("desktop")
}
""")
        self.kt("feature/headless", "headless", "HeadlessWorker.kt", """
package @PKG@.headless

class HeadlessWorker {
    fun run(): Int = 0
}
""")

    # ── a feature that never moved under feature/ ───────────────────────────
    def _root_level_feature(self) -> None:
        # Also depends on :core:netcall — the SECOND feature consumer, which is what
        # makes that module shared app data under the DRY corollary rather than
        # single-feature remote that stays put.
        self.w("oldscreen/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    sourceSets {
        commonMain.dependencies {
            implementation(project(":core:model"))
            implementation(project(":core:netcall"))
        }
    }
}
""")
        self.kt("oldscreen", "oldscreen", "OldScreen.kt", """
package @PKG@.oldscreen

import androidx.compose.runtime.Composable

@Composable
fun OldScreen() = Unit
""")

    # ── two features that depend on each other → dependency-cycle ───────────
    def _cycle(self) -> None:
        for a, b in (("cyclea", "cycleb"), ("cycleb", "cyclea")):
            self.w(f"feature/{a}/build.gradle.kts", """
kotlin {
    androidTarget()
    iosArm64()
    jvm("desktop")
    sourceSets { commonMain.dependencies { implementation(project(":feature:@OTHER@")) } }
}
""".replace("@OTHER@", b))
            self.kt(f"feature/{a}", a, f"{a.capitalize()}Screen.kt", """
package @PKG@.@SELF@

import androidx.compose.runtime.Composable

@Composable
fun @CAP@Screen() = Unit
""".replace("@SELF@", a).replace("@CAP@", a.capitalize()))


def file_snapshot(root: Path) -> dict[str, int]:
    """Every file under `root` with its size — the before/after side-effect check."""
    return {
        p.relative_to(root).as_posix(): p.stat().st_size
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Fixture(Path(tmp))
        fixture.build()

        before = file_snapshot(fixture.root)
        proc = subprocess.run(
            [sys.executable, str(DISCOVER), "--root", str(fixture.root), "--json-only"],
            capture_output=True,
            text=True,
        )
        after = file_snapshot(fixture.root)

        if proc.returncode != 0:
            print(f"discover exited {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
            return 1
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print(f"discover did not emit JSON:\n{proc.stdout[:2000]}\n{proc.stderr}")
            return 1

        failures: list[str] = []

        def want(condition: bool, message: str) -> None:
            if not condition:
                failures.append(message)

        # ── the no-side-effects guarantee ───────────────────────────────────
        if before != after:
            added = sorted(set(after) - set(before))
            changed = sorted(k for k in before if k in after and before[k] != after[k])
            removed = sorted(set(before) - set(after))
            failures.append(
                f"discovery wrote to the target repo — added {added}, changed {changed}, "
                f"removed {removed}"
            )

        # ── project identity ────────────────────────────────────────────────
        p = report["project"]
        want(p["rootProjectName"] == "ProbeCo", f"rootProjectName {p['rootProjectName']!r}")
        want(p["packagePrefix"] == PKG, f"packagePrefix {p['packagePrefix']!r}, expected {PKG!r}")
        want(p["role"] == "adopted", f"role {p['role']!r}, expected 'adopted'")
        want(p["migrationTarget"] is True, "an adopted repo must be a migration target")
        want(p["appModule"] == "shared", f"appModule {p['appModule']!r}")

        # ── module kinds, and the commented-out include ──────────────────────
        kinds = {m["gradlePath"]: m["kind"] for m in report["modules"]}
        want(":feature:ghost" not in kinds, "a commented-out include became a module")
        for path, expected in {
            ":shared": "app",
            ":core:common": "core-kmpilot",
            ":core:data": "core-kmpilot",
            ":core:designsystem": "core-kmpilot",
            ":core:model": "core-host",
            ":core:netcall": "core-host",
            ":core:androidutil": "core-host",
            ":feature:conforming": "feature",
            ":feature:messy": "feature",
            ":oldscreen": "feature",
        }.items():
            want(kinds.get(path) == expected, f"{path} kind {kinds.get(path)!r}, expected {expected!r}")

        # ── targets, including the `iosArm64 { }` block form ────────────────
        targets = {m["gradlePath"]: set(m["targets"]) for m in report["modules"]}
        want(
            targets.get(":core:common") == {"android", "ios", "desktop"},
            f":core:common targets {sorted(targets.get(':core:common', []))} — the "
            "`iosArm64 { }` block form must be detected",
        )
        want("desktop" not in targets.get(":feature:messy", set()), ":feature:messy has no desktop target")

        features = {f["gradlePath"]: f for f in report["features"]}
        for path in (":feature:conforming", ":feature:messy", ":feature:legacy",
                     ":feature:headless", ":oldscreen"):
            want(path in features, f"{path} was not reported as a feature")

        # ── false-positive guard: the three traps ───────────────────────────
        conforming = features.get(":feature:conforming", {})
        want(
            conforming.get("androidEvidence") == [],
            "the conforming feature produced Android evidence (androidx.navigation / "
            f"androidx.lifecycle.ViewModel / androidMain must not count): "
            f"{conforming.get('androidEvidence')}",
        )
        want(
            conforming.get("entryPoint", {}).get("composable") == "ConformingScreen",
            f"entry point {conforming.get('entryPoint')}, expected ConformingScreen",
        )
        want(
            conforming.get("verdict") == "conforming",
            f"conforming feature verdict {conforming.get('verdict')!r} with findings "
            f"{conforming.get('findings')}",
        )

        # ── refusals ────────────────────────────────────────────────────────
        refusals = {r["subject"]: r for r in report["refusals"]}
        want(":feature:legacy" in refusals, "the Android-locked feature was not refused")
        legacy_reason = refusals.get(":feature:legacy", {}).get("reason", "")
        for api in ("Retrofit", "Hilt", "LiveData", "Android framework"):
            want(api in legacy_reason, f"the legacy refusal does not name {api}: {legacy_reason!r}")
        want(
            not any("LegacyNotes" in e for e in refusals.get(":feature:legacy", {}).get("evidence", [])),
            "a commented-out import produced refusal evidence",
        )
        want(":feature:headless" in refusals, "the feature with no composable was not refused")
        want(
            "no screen entry point" in refusals.get(":feature:headless", {}).get("reason", ""),
            f"headless refusal reason {refusals.get(':feature:headless', {}).get('reason')!r}",
        )
        want(":core:androidutil" in refusals, "the unhoistable shared module was not refused")
        want(
            ":feature:messy" in refusals.get(":core:androidutil", {}).get("blocks", []),
            "the unhoistable refusal does not name the features it blocks",
        )
        want(
            ":feature:conforming" not in refusals and ":core:model" not in refusals,
            "clean subjects were refused",
        )

        # ── tier proposals: all four outcomes ───────────────────────────────
        shared = {s["gradlePath"]: s for s in report["shared"]}
        for path, tier in {
            ":core:model": "common",
            ":core:netcall": "data",
            ":core:widgets": "designsystem",
            ":core:mixed": "split",
        }.items():
            got = shared.get(path, {}).get("proposedTier")
            want(got == tier, f"{path} proposed tier {got!r}, expected {tier!r}")
        want(
            "DRY corollary" in shared.get(":core:netcall", {}).get("reason", ""),
            "a shared data module used by 2+ features must cite the DRY corollary: "
            f"{shared.get(':core:netcall', {}).get('reason')!r}",
        )
        want(
            shared.get(":core:mixed", {}).get("filesByTier", {}).keys()
            >= {"common", "data", "designsystem"},
            "the split proposal must break down per tier: "
            f"{shared.get(':core:mixed', {}).get('filesByTier')}",
        )
        want(shared.get(":core:model", {}).get("hoistable") is True, ":core:model should be hoistable")
        want(
            shared.get(":core:androidutil", {}).get("hoistable") is False,
            ":core:androidutil must not be hoistable",
        )

        # ── shared code living inside a feature ─────────────────────────────
        in_feature = {(s["consumer"], s["owner"]): s for s in report["inFeatureShared"]}
        edge = in_feature.get((":feature:messy", ":feature:conforming"))
        want(edge is not None, "the cross-feature import was not reported as in-feature shared code")
        if edge:
            want(
                any("ConformingRepository" in s for s in edge["symbols"]),
                f"in-feature symbols {edge['symbols']}",
            )
            want(
                any("ConformingRepository.kt" in f for f in edge["declaredIn"]),
                f"declaredIn {edge['declaredIn']} — the proposal must be scoped to the "
                "declaring file, not the whole package",
            )
        want(
            not any(c == ":shared" for c, _ in in_feature),
            "the app module importing a feature screen is Integration Point 4, not shared code",
        )

        # ── notes ───────────────────────────────────────────────────────────
        notes = {}
        for n in report["notes"]:
            notes.setdefault(n["id"], []).append(n["subject"])
        for note_id, subject in {
            "feature-outside-featuredir": ":oldscreen",
            "missing-desktop-target": ":feature:messy",
            "cross-feature-dependency": ":feature:messy → :feature:conforming",
            "catalog-split": None,
            "dependency-cycle": None,
        }.items():
            want(note_id in notes, f"note {note_id} did not fire")
            if subject is not None:
                want(
                    subject in notes.get(note_id, []),
                    f"note {note_id} fired on {notes.get(note_id)}, expected {subject!r}",
                )
        want(
            "not-adopted" not in notes and "template-mode" not in notes,
            f"an adopted repo got the wrong role note: {sorted(notes)}",
        )
        cycle_note = notes.get("dependency-cycle", [""])[0]
        want(
            "cyclea" in cycle_note and "cycleb" in cycle_note,
            f"the cycle note names {cycle_note!r}, expected both cycle members",
        )

        # ── order: dependencies before consumers ────────────────────────────
        order = report["graph"]["order"]
        for dependency, consumer in ((":core:model", ":feature:messy"),
                                     (":core:netcall", ":feature:messy"),
                                     (":core:model", ":oldscreen")):
            want(
                dependency in order and consumer in order
                and order.index(dependency) < order.index(consumer),
                f"{dependency} must be ordered before {consumer}: {order}",
            )
        want(
            ":feature:cyclea" not in order and ":feature:cycleb" not in order,
            f"cycle members must be held out of the order, not linearised: {order}",
        )
        want(
            any(set(c) == {":feature:cyclea", ":feature:cycleb"} for c in report["graph"]["cycles"]),
            f"cycles {report['graph']['cycles']}",
        )

        summary = report["summary"]
        print(
            f"fixture: {summary['modules']} modules · {summary['features']} features · "
            f"{summary['refused']} refusals · {summary['sharedPackages']} shared "
            f"({summary['hoistable']} hoistable) · {summary['notes']} notes"
        )
        print(f"refusals: {', '.join(sorted(refusals))}")
        print(f"notes: {', '.join(sorted(notes))}")

        if failures:
            print("\nFAILURES:")
            for f in failures:
                print(f"  x {f}")
            return 1
        print("\nPASS — every classifier fires, traps stay silent, nothing was written")
        return 0


if __name__ == "__main__":
    sys.exit(main())
