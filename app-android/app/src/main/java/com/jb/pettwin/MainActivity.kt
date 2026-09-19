package com.jb.pettwin

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.*
import android.widget.Toast
import java.io.File

class MainActivity : Activity() {

    private lateinit var webView: WebView
    private var pendingPermissionRequest: PermissionRequest? = null

    // JS 桥：状态持久化 + toast + server 地址存取
    private val androidBridge = object {
        @JavascriptInterface
        fun loadState(key: String): String {
            val f = File(filesDir, key + ".json")
            return if (f.exists()) f.readText() else "{}"
        }

        @JavascriptInterface
        fun saveState(key: String, json: String) {
            File(filesDir, key + ".json").writeText(json)
        }

        @JavascriptInterface
        fun toast(msg: String) {
            runOnUiThread { Toast.makeText(this@MainActivity, msg, Toast.LENGTH_SHORT).show() }
        }

        @JavascriptInterface
        fun openUrl(url: String) {
            runOnUiThread { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        setContentView(webView)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(req: PermissionRequest) {
                // getUserMedia(摄像头/麦克风) → 直接授予（App 启动时已申请系统权限）
                runOnUiThread {
                    val wanted = req.resources
                    val grant = wanted.filter {
                        it == PermissionRequest.RESOURCE_VIDEO_CAPTURE ||
                            it == PermissionRequest.RESOURCE_AUDIO_CAPTURE
                    }
                    if (grant.isNotEmpty()) {
                        req.grant(grant.toTypedArray())
                    } else {
                        req.deny()
                    }
                }
            }

            override fun onPermissionRequestCanceled(req: PermissionRequest) {
                pendingPermissionRequest = null
            }
        }
        webView.addJavascriptInterface(androidBridge, "AndroidBridge")

        // server 地址：默认本机局域网地址，App 内可改（localStorage 兜底）
        // 首次进 assets 内置页；页内可切到远程 server 的 mobile 页
        webView.loadUrl("file:///android_asset/web/index.html")
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    @Deprecated("Deprecated in favor of ActivityResult APIs")
    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        pendingPermissionRequest?.grant(pendingPermissionRequest?.resources)
        pendingPermissionRequest = null
    }
}
