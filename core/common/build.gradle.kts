plugins {
    alias(libs.plugins.kotlinMultiplatform)
    alias(libs.plugins.androidKotlinMultiplatformLibrary)
    alias(libs.plugins.kotlinSerialization)
    alias(libs.plugins.jetbrainsCompose)
    alias(libs.plugins.composeCompiler)
}

kotlin {
    compilerOptions {
        // LocalAppLocale is an `expect object` (a classifier); expect/actual
        // classifiers are Beta and require this flag to suppress the warning.
        freeCompilerArgs.add("-Xexpect-actual-classes")
    }

// Target declarations - add or remove as needed below. These define
// which platforms this KMP module supports.
// See: https://kotlinlang.org/docs/multiplatform-discover-project.html#targets
    android {
        namespace = "thisissadeghi.common"
    }
    jvm("desktop")

// For iOS targets, this is also where you should
// configure native binary output. For more information, see:
// https://kotlinlang.org/docs/multiplatform-build-native-binaries.html#build-xcframeworks

// A step-by-step guide on how to include this library in an XCode
// project can be found here:
// https://developer.android.com/kotlin/multiplatform/migrate
    val xcfName = "common"

    iosArm64 {
        binaries.framework {
            baseName = xcfName
        }
    }

    iosSimulatorArm64 {
        binaries.framework {
            baseName = xcfName
        }
    }

// Source set declarations.
// Declaring a target automatically creates a source set with the same name. By default, the
// Kotlin Gradle Plugin creates additional source sets that depend on each other, since it is
// common to share sources between related targets.
// See: https://kotlinlang.org/docs/multiplatform-hierarchy.html
    sourceSets {
        commonMain {
            dependencies {
                implementation(libs.compose.components.resources)
                implementation(libs.compose.ui)
                implementation(libs.kotlinCollection)
                implementation(libs.kotlinxSerialization)
                api(libs.androidx.lifecycle.viewmodel)
                api(libs.androidx.lifecycle.runtime.compose)
                // `api`, like its two siblings above: a migrated feature that has not
                // been Koin-wired yet still calls the CMP `viewModel()` factory, and
                // every feature module already depends on :core:common. Exporting it
                // here fixes that once instead of in every carved build file.
                api(libs.androidx.lifecycle.viewmodel.compose)

                api(libs.koin.core)
                implementation(libs.koin.compose)
                implementation(libs.kotlinx.datetime)
            }
        }

        androidMain {
            dependencies {
                implementation(libs.koin.android)
                implementation(libs.coreKtx)
            }
        }

        iosMain {
            dependencies {}
        }
    }
}
