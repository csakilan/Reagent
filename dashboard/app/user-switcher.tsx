"use client";
import { useState } from "react";
import { getDemoUser, setDemoUser } from "../lib/api";

export default function UserSwitcher() {
  const [user, setUser] = useState(getDemoUser());
  return <label className="user-switch">LOGIN AS<select aria-label="Login as" value={user} onChange={event => { setUser(event.target.value); setDemoUser(event.target.value); }}><option value="alice">Alice · approver</option><option value="bob">Bob · platform-eng</option><option value="carol">Carol · platform-eng</option></select></label>;
}
