/* Sample data for the hosted demo. Generated from a demo home; no real people or secrets. */
window.AMUL_DEMO_DATA = {
 "state": {
  "addresses": [
   {
    "pincode": "110001",
    "label": "Home",
    "short": "New Delhi",
    "enabled": true,
    "products": [
     "amul-milk-shake-premix-mango-500-g",
     "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
     "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
    ],
    "disabled_products": []
   },
   {
    "pincode": "560001",
    "label": "Mum's place",
    "short": "Bangalore",
    "enabled": true,
    "products": [
     "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
     "amul-high-protein-buttermilk-200-ml-or-pack-of-30"
    ],
    "disabled_products": []
   }
  ],
  "products": [
   {
    "alias": "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
    "short": "rose-lassi",
    "label": "High Protein Rose Lassi",
    "enabled": true,
    "enquiry_name": ""
   },
   {
    "alias": "amul-high-protein-plain-lassi-200-ml-or-pack-of-30",
    "short": "plain-lassi",
    "label": "High Protein Plain Lassi",
    "enabled": true,
    "enquiry_name": ""
   },
   {
    "alias": "amul-high-protein-buttermilk-200-ml-or-pack-of-30",
    "short": "buttermilk",
    "label": "High Protein Buttermilk",
    "enabled": true,
    "enquiry_name": ""
   },
   {
    "alias": "amul-milk-shake-premix-mango-500-g",
    "short": "mango",
    "label": "Milk Shake Premix Mango",
    "enabled": true,
    "enquiry_name": ""
   }
  ],
  "recipients": [
   {
    "name": "desktop",
    "type": "desktop",
    "enabled": true,
    "config": {},
    "summary": "desktop: desktop",
    "problems": []
   },
   {
    "name": "browser",
    "type": "browser",
    "enabled": true,
    "config": {},
    "summary": "browser: browser",
    "problems": []
   },
   {
    "name": "me-phone",
    "type": "ntfy",
    "enabled": true,
    "config": {
     "topic": "amul-watch-k7p2x9q4"
    },
    "summary": "me-phone: ntfy topic amul-watch-k7p2x9q4",
    "problems": []
   },
   {
    "name": "mum-telegram",
    "type": "telegram",
    "enabled": true,
    "config": {
     "bot_token": "${TELEGRAM_BOT_TOKEN}",
     "chat_id": "123456789"
    },
    "summary": "mum-telegram: telegram 123456789",
    "problems": []
   },
   {
    "name": "family-email",
    "type": "email",
    "enabled": true,
    "config": {
     "to": [
      "family@example.com"
     ]
    },
    "summary": "family-email: email family@example.com",
    "problems": []
   }
  ],
  "watches": [
   {
    "pincode": "110001",
    "address_label": "Home",
    "alias": "amul-high-protein-plain-lassi-200-ml-or-pack-of-30",
    "product_short": "plain-lassi",
    "product_label": "High Protein Plain Lassi",
    "recipients": [
     "me-phone",
     "family-email"
    ],
    "recipients_summary": [
     "me-phone: ntfy topic amul-watch-k7p2x9q4",
     "family-email: email family@example.com"
    ],
    "route_key": "110001:plain-lassi",
    "enabled": true,
    "explicit": true,
    "has_route": true
   },
   {
    "pincode": "110001",
    "address_label": "Home",
    "alias": "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
    "product_short": "rose-lassi",
    "product_label": "High Protein Rose Lassi",
    "recipients": [
     "me-phone"
    ],
    "recipients_summary": [
     "me-phone: ntfy topic amul-watch-k7p2x9q4"
    ],
    "route_key": "110001:rose-lassi",
    "enabled": true,
    "explicit": true,
    "has_route": true
   },
   {
    "pincode": "110001",
    "address_label": "Home",
    "alias": "amul-milk-shake-premix-mango-500-g",
    "product_short": "mango",
    "product_label": "Milk Shake Premix Mango",
    "recipients": [
     "me-phone"
    ],
    "recipients_summary": [
     "me-phone: ntfy topic amul-watch-k7p2x9q4"
    ],
    "route_key": "110001:mango",
    "enabled": true,
    "explicit": true,
    "has_route": true
   },
   {
    "pincode": "560001",
    "address_label": "Mum's place",
    "alias": "amul-high-protein-buttermilk-200-ml-or-pack-of-30",
    "product_short": "buttermilk",
    "product_label": "High Protein Buttermilk",
    "recipients": [
     "mum-telegram"
    ],
    "recipients_summary": [
     "mum-telegram: telegram 123456789"
    ],
    "route_key": "560001:buttermilk",
    "enabled": true,
    "explicit": true,
    "has_route": true
   },
   {
    "pincode": "560001",
    "address_label": "Mum's place",
    "alias": "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
    "product_short": "rose-lassi",
    "product_label": "High Protein Rose Lassi",
    "recipients": [
     "me-phone",
     "mum-telegram"
    ],
    "recipients_summary": [
     "me-phone: ntfy topic amul-watch-k7p2x9q4",
     "mum-telegram: telegram 123456789"
    ],
    "route_key": "560001:rose-lassi",
    "enabled": true,
    "explicit": true,
    "has_route": true
   }
  ],
  "default_alerts": [
   "desktop"
  ],
  "product_shorthands": [
   "buttermilk",
   "mango",
   "plain-lassi",
   "rose-lassi"
  ],
  "notifier_types": [
   {
    "type": "email",
    "description": "email via SMTP (Gmail app password works)",
    "required": [
     "to"
    ]
   },
   {
    "type": "ntfy",
    "description": "push to the ntfy phone app, no account needed",
    "required": [
     "topic"
    ]
   },
   {
    "type": "telegram",
    "description": "Telegram bot message",
    "required": [
     "bot_token",
     "chat_id"
    ]
   },
   {
    "type": "discord",
    "description": "Discord channel webhook",
    "required": [
     "url"
    ]
   },
   {
    "type": "slack_webhook",
    "description": "Slack incoming webhook",
    "required": [
     "url"
    ]
   },
   {
    "type": "slack",
    "description": "Slack bot token, channel or person",
    "required": [
     "token",
     "channel"
    ]
   },
   {
    "type": "webhook",
    "description": "POST the alert as JSON to any URL",
    "required": [
     "url"
    ]
   },
   {
    "type": "command",
    "description": "run a local command with the alert in env vars",
    "required": [
     "command"
    ]
   },
   {
    "type": "desktop",
    "description": "desktop notification on this machine",
    "required": []
   },
   {
    "type": "browser",
    "description": "open the product page in the browser",
    "required": []
   }
  ],
  "system_alerts": [
   "desktop"
  ],
  "qty_update_alerts": [],
  "config_paths": {
   "config": "~/.config/amul-watch/config.yaml",
   "notifications": "~/.config/amul-watch/notifications.yaml"
  }
 },
 "stock": [
  {
   "pincode": "110001",
   "location": "New Delhi 110001",
   "product": "High Protein Plain Lassi",
   "alias": "amul-high-protein-plain-lassi-200-ml-or-pack-of-30",
   "in_stock": "no",
   "qty": "0",
   "variant": "",
   "price": ""
  },
  {
   "pincode": "110001",
   "location": "New Delhi 110001",
   "product": "High Protein Rose Lassi",
   "alias": "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
   "in_stock": "no",
   "qty": "0",
   "variant": "",
   "price": ""
  },
  {
   "pincode": "110001",
   "location": "New Delhi 110001",
   "product": "Milk Shake Premix Mango",
   "alias": "amul-milk-shake-premix-mango-500-g",
   "in_stock": "yes",
   "qty": "7",
   "variant": "Amul Milk Shake Premix Mango, 500 g | MSPMCP01_01",
   "price": "210"
  },
  {
   "pincode": "560001",
   "location": "Bangalore 560001",
   "product": "High Protein Buttermilk",
   "alias": "amul-high-protein-buttermilk-200-ml-or-pack-of-30",
   "in_stock": "yes",
   "qty": "67",
   "variant": "Amul High Protein Buttermilk, 200 mL | Pack of 30 | BTMCP11_30",
   "price": "900"
  },
  {
   "pincode": "560001",
   "location": "Bangalore 560001",
   "product": "High Protein Rose Lassi",
   "alias": "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
   "in_stock": "no",
   "qty": "0",
   "variant": "",
   "price": ""
  }
 ]
};
