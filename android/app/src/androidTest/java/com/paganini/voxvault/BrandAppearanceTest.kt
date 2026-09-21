package com.paganini.voxvault

import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.drawable.AdaptiveIconDrawable
import android.view.ContextThemeWrapper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class BrandAppearanceTest {
    @Test
    fun transparentBrandingFollowsThemeAndFitsToolbar() {
        val base = InstrumentationRegistry.getInstrumentation().targetContext
        for (night in listOf(false, true)) {
            val config = Configuration(base.resources.configuration).apply {
                uiMode = (uiMode and Configuration.UI_MODE_NIGHT_MASK.inv()) or
                    if (night) Configuration.UI_MODE_NIGHT_YES else Configuration.UI_MODE_NIGHT_NO
            }
            val context = ContextThemeWrapper(base.createConfigurationContext(config), R.style.Theme_VoxVault)
            val density = context.resources.displayMetrics.density
            val logo = context.getDrawable(R.drawable.voxvault_logo)!!
            assertEquals(24, (logo.intrinsicWidth / density).toInt())
            assertEquals(24, (logo.intrinsicHeight / density).toInt())
            val expected = if (night) Color.WHITE else Color.BLACK
            for ((id, color) in listOf(
                R.drawable.voxvault_logo to expected,
                R.drawable.ic_launcher_foreground to expected,
                R.drawable.ic_launcher_monochrome to Color.WHITE,
                R.drawable.ic_notification to Color.WHITE,
            )) {
                val drawable = context.getDrawable(id)!!
                val size = if (id == R.drawable.voxvault_logo || id == R.drawable.ic_notification) logo.intrinsicWidth else 432
                val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
                drawable.setBounds(0, 0, size, size)
                drawable.draw(Canvas(bitmap))
                val pixels = IntArray(size * size)
                bitmap.getPixels(pixels, 0, size, 0, 0, size, size)
                val solid = pixels.filter { Color.alpha(it) > 240 }
                assertTrue("Resource $id must contain the mark", solid.size > size * size / 100)
                assertTrue("Resource $id must have transparent negative space", pixels.count { Color.alpha(it) == 0 } > pixels.size * 0.7)
                // Android's premultiplied edge pixels can round a white channel to 254.
                assertTrue("${context.resources.getResourceEntryName(id)} must follow night=$night", solid.all {
                    kotlin.math.abs(Color.red(it) - Color.red(color)) <= 1 &&
                        kotlin.math.abs(Color.green(it) - Color.green(color)) <= 1 &&
                        kotlin.math.abs(Color.blue(it) - Color.blue(color)) <= 1
                })
                bitmap.recycle()
            }
            val launcher = context.getDrawable(R.mipmap.ic_launcher) as AdaptiveIconDrawable
            assertTrue(launcher.foreground != null && launcher.background != null)
        }
    }
}
