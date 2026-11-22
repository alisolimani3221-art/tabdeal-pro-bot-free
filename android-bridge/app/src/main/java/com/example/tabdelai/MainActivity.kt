package com.example.tabdelai

import android.app.Activity
import android.os.Bundle
import android.graphics.Typeface
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var output: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 40, 28, 28)
            gravity = Gravity.TOP
        }

        val title = TextView(this).apply {
            text = "Tabdeal AI Bridge"
            textSize = 26f
            setTypeface(null, Typeface.BOLD)
        }
        status = TextView(this).apply {
            text = "وضعیت: آماده اتصال آزمایشی"
            textSize = 16f
            setPadding(0, 24, 0, 24)
        }
        val connect = Button(this).apply {
            text = "بررسی ارتباط بازار (نمونه)"
            setOnClickListener {
                status.text = "وضعیت: نمونه محلی فعال است؛ API هنوز پیکربندی نشده"
                output.text = "برای اتصال واقعی، نشانی رسمی API عمومی بازار و یک سرویس واسط امن لازم است."
            }
        }
        val analyze = Button(this).apply {
            text = "نمایش نمونه درخواست تحلیل"
            setOnClickListener {
                output.text = "درخواست نمونه به هوش مصنوعی:\n«اطلاعات بازار را فقط تحلیل کن؛ هیچ سفارشی ثبت نکن. ریسک‌ها و عدم قطعیت را توضیح بده.»"
            }
        }
        output = TextView(this).apply {
            text = "نتیجه و پاسخ هوش مصنوعی اینجا نمایش داده می‌شود."
            textSize = 16f
            setPadding(0, 28, 0, 0)
        }
        page.addView(title)
        page.addView(status)
        page.addView(connect)
        page.addView(analyze)
        page.addView(output)
        setContentView(page)
    }
}
