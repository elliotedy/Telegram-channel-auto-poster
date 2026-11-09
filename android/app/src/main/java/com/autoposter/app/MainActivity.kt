package com.autoposter.app

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

private const val PREFS = "autoposter"

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Scaffold { padding -> ManagerApp(Modifier.padding(padding)) }
            }
        }
    }
}

private fun prefs(context: Context) =
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

private suspend fun httpGet(baseUrl: String, path: String, apiKey: String): JSONObject =
    withContext(Dispatchers.IO) {
        val conn = (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            if (apiKey.isNotBlank()) setRequestProperty("X-API-Key", apiKey)
            connectTimeout = 10000
            readTimeout = 10000
        }
        val text = conn.inputStream.bufferedReader().readText()
        conn.disconnect()
        JSONObject(text)
    }

private suspend fun httpPost(
    baseUrl: String,
    path: String,
    apiKey: String,
    body: JSONObject,
): JSONObject = withContext(Dispatchers.IO) {
    val conn = (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
        requestMethod = "POST"
        setRequestProperty("Content-Type", "application/json")
        if (apiKey.isNotBlank()) setRequestProperty("X-API-Key", apiKey)
        doOutput = true
        connectTimeout = 10000
        readTimeout = 10000
    }
    conn.outputStream.use { it.write(body.toString().toByteArray()) }
    val text = conn.inputStream.bufferedReader().readText()
    conn.disconnect()
    JSONObject(text)
}

private suspend fun httpDelete(baseUrl: String, path: String, apiKey: String): JSONObject =
    withContext(Dispatchers.IO) {
        val conn = (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "DELETE"
            if (apiKey.isNotBlank()) setRequestProperty("X-API-Key", apiKey)
            connectTimeout = 10000
            readTimeout = 10000
        }
        val text = conn.inputStream.bufferedReader().readText()
        conn.disconnect()
        JSONObject(text)
    }

@Composable
fun ManagerApp(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var baseUrl by remember {
        mutableStateOf(prefs(context).getString("base_url", "http://192.168.1.10:8000").orEmpty())
    }
    var apiKey by remember {
        mutableStateOf(prefs(context).getString("api_key", "").orEmpty())
    }
    var status by remember { mutableStateOf<JSONObject?>(null) }
    var channels by remember { mutableStateOf<JSONArray?>(null) }
    var media by remember { mutableStateOf<JSONArray?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var tab by remember { mutableStateOf(0) }

    suspend fun refresh() {
        try {
            error = null
            status = httpGet(baseUrl, "/api/status", apiKey)
            channels = httpGet(baseUrl, "/api/channels", apiKey).optJSONArray("channels")
            media = httpGet(baseUrl, "/api/media", apiKey).optJSONArray("media")
        } catch (e: Exception) {
            error = e.message ?: "Connection failed"
        }
    }

    LaunchedEffect(baseUrl, apiKey) { refresh() }

    Column(modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Telegram Auto Poster", style = MaterialTheme.typography.headlineSmall)

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { tab = 0 }) { Text("Status") }
            TextButton(onClick = { tab = 1 }) { Text("Channels") }
            TextButton(onClick = { tab = 2 }) { Text("Media") }
            TextButton(onClick = { tab = 3 }) { Text("Settings") }
        }

        if (error != null) Text(error!!, color = MaterialTheme.colorScheme.error)

        when (tab) {
            0 -> {
                val s = status
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Channels: ${s?.optInt("active_channels", 0)} / ${s?.optInt("channels", 0)}")
                        Text("Media items: ${s?.optInt("media", 0)}")
                        Text("Schedule: ${s?.optString("schedule_times", "-")}")
                        Text("Timezone: ${s?.optString("timezone", "-")}")
                        Text("Paused: ${s?.optBoolean("paused", false)}")
                        Text("Next post: ${s?.optString("next_post_at", "-")}")
                    }
                }
                Button(onClick = { scope.launch { refresh() } }, modifier = Modifier.fillMaxWidth()) {
                    Text("Refresh")
                }
            }
            1 -> {
                var chatId by remember { mutableStateOf("") }
                var title by remember { mutableStateOf("") }
                var username by remember { mutableStateOf("") }
                OutlinedTextField(chatId, { chatId = it }, label = { Text("Chat ID e.g. -100123") })
                OutlinedTextField(title, { title = it }, label = { Text("Title") })
                OutlinedTextField(username, { username = it }, label = { Text("Username (optional)") })
                Button(
                    onClick = {
                        scope.launch {
                            try {
                                httpPost(
                                    baseUrl,
                                    "/api/channels",
                                    apiKey,
                                    JSONObject()
                                        .put("chat_id", chatId.toLong())
                                        .put("title", title)
                                        .put("username", username),
                                )
                                refresh()
                            } catch (e: Exception) {
                                error = e.message
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Add channel") }
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    val arr = channels
                    if (arr != null) {
                        items(arr.length()) { i ->
                            val c = arr.getJSONObject(i)
                            Card(Modifier.fillMaxWidth()) {
                                Row(Modifier.padding(12.dp)) {
                                    Column(Modifier.weight(1f)) {
                                        Text(c.optString("title", "Channel"))
                                        Text(c.optString("username", ""))
                                        Text(c.optLong("chat_id").toString())
                                    }
                                    TextButton(
                                        onClick = {
                                            scope.launch {
                                                try {
                                                    httpPost(
                                                        baseUrl,
                                                        "/api/channels/${c.optLong("chat_id")}/toggle",
                                                        apiKey,
                                                        JSONObject().put("active", c.optInt("active", 1) != 1),
                                                    )
                                                    refresh()
                                                } catch (e: Exception) { error = e.message }
                                            }
                                        },
                                    ) { Text(if (c.optInt("active", 1) == 1) "Pause" else "Resume") }
                                    TextButton(
                                        onClick = {
                                            scope.launch {
                                                try {
                                                    httpDelete(baseUrl, "/api/channels/${c.optLong("chat_id")}", apiKey)
                                                    refresh()
                                                } catch (e: Exception) { error = e.message }
                                            }
                                        },
                                    ) { Text("Delete") }
                                }
                            }
                        }
                    }
                }
            }
            2 -> {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    val arr = media
                    if (arr != null) {
                        items(arr.length()) { i ->
                            val m = arr.getJSONObject(i)
                            Card(Modifier.fillMaxWidth()) {
                                Column(Modifier.padding(12.dp)) {
                                    Text(m.optString("caption", m.optString("filename", "Media")))
                                    Text("${m.optString("media_type", "")} • posted ${m.optInt("posted_count", 0)}x")
                                }
                            }
                        }
                    }
                }
            }
            3 -> {
                OutlinedTextField(baseUrl, { baseUrl = it }, label = { Text("API URL e.g. http://192.168.1.10:8000") })
                OutlinedTextField(apiKey, { apiKey = it }, label = { Text("API key") })
                Button(
                    onClick = {
                        prefs(context).edit().putString("base_url", baseUrl).putString("api_key", apiKey).apply()
                        scope.launch { refresh() }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Save & connect") }
                Spacer(Modifier.height(8.dp))
                Text("Enable API in the bot .env with API_ENABLED=true and set API_KEY.")
            }
        }
    }
}
