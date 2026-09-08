package com.paganini.voxvault

import android.annotation.SuppressLint
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.TooltipCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.appbar.MaterialToolbar
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

class ExplorerActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private var archiveUrl: HttpUrl? = null
    private var loadFailed = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_explorer)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.explorerRoot)) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        findViewById<MaterialToolbar>(R.id.explorerToolbar).setNavigationOnClickListener {
            onBackPressedDispatcher.onBackPressed()
        }
        findViewById<View>(R.id.refreshArchive).setOnClickListener { loadArchive() }
        TooltipCompat.setTooltipText(findViewById(R.id.refreshArchive), getString(R.string.refresh))
        findViewById<View>(R.id.retryArchive).setOnClickListener { loadArchive() }
        findViewById<View>(R.id.archiveSettings).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        webView = findViewById(R.id.archiveWebView)
        webView.settings.apply {
            javaScriptEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            cacheMode = WebSettings.LOAD_NO_CACHE
        }
        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val target = request.url.toString().toHttpUrlOrNull() ?: return true
                val configured = archiveUrl ?: return true
                return target.scheme != configured.scheme || target.host != configured.host ||
                    target.port != configured.port || target.encodedPath != configured.encodedPath
            }

            override fun onPageFinished(view: WebView, url: String) {
                findViewById<View>(R.id.archiveProgress).visibility = View.GONE
                if (!loadFailed) webView.visibility = View.VISIBLE
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) showError(getString(R.string.archive_unavailable))
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                if (request.isForMainFrame) showError(getString(R.string.archive_http_error, response.statusCode))
            }
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                webView.evaluateJavascript(
                    "(function(){if(window.voxvaultBack)return window.voxvaultBack();" +
                        "var dialogs=document.querySelectorAll('dialog[open]');" +
                        "if(!dialogs.length)return false;var dialog=dialogs[dialogs.length-1];" +
                        "dialog.returnValue='cancel';dialog.close();return true;})()"
                ) { closed -> if (closed != "true") finish() }
            }
        })
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
        lifecycleScope.launch {
            val settings = SettingsManager(this@ExplorerActivity)
            try {
                val url = BackendUrl.resolve(settings.backendUrlFlow.first(), settings.backendPortFlow.first(), "ui/explorer")
                    .newBuilder().addQueryParameter("app", "1").build()
                if (url != archiveUrl) {
                    archiveUrl = url
                    loadArchive()
                }
            } catch (error: IllegalArgumentException) {
                archiveUrl = null
                showError(error.message ?: getString(R.string.archive_unavailable))
            } catch (_: java.net.URISyntaxException) {
                archiveUrl = null
                showError(getString(R.string.invalid_server_address))
            }
        }
    }

    private fun loadArchive() {
        val url = archiveUrl ?: return
        loadFailed = false
        findViewById<View>(R.id.archiveError).visibility = View.GONE
        findViewById<View>(R.id.archiveProgress).visibility = View.VISIBLE
        webView.visibility = View.INVISIBLE
        webView.loadUrl(url.toString())
    }

    private fun showError(message: String) {
        loadFailed = true
        webView.stopLoading()
        webView.visibility = View.GONE
        findViewById<View>(R.id.archiveProgress).visibility = View.GONE
        findViewById<View>(R.id.archiveError).visibility = View.VISIBLE
        findViewById<TextView>(R.id.archiveErrorMessage).text = message
        findViewById<View>(R.id.retryArchive).isEnabled = archiveUrl != null
    }

    override fun onPause() {
        webView.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        (webView.parent as? ViewGroup)?.removeView(webView)
        webView.destroy()
        super.onDestroy()
    }
}
